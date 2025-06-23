import os
import re
import time
import time
import base64
import faiss
import numpy as np
from email.mime.text import MIMEText
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.auth.transport.requests import requests
from sentence_transformers import SentenceTransformer

# Gmail API scope for reading emails and creating drafts
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

# Initialize the sentence transformer model for embeddings
model = SentenceTransformer('all-MiniLM-L6-v2')

# Define email templates with {sender_name} placeholder
EMAIL_TEMPLATES = [
    """
Dear {sender_name},

Thank you for your email regarding

We will schedule a meeting at your earliest convenience to discuss this further.

Best regards,
[Your Name]
    """,  # Template 1: Meeting request
    """
Dear {sender_name},

Thank you for reaching out with your support inquiry. We have received your request and will respond within 24-48 hours.

Best regards,
[Your Name]
    """,  # Template 2: Support inquiry
    """
Dear {sender_name},

Thank you for your email. I'll get back to you soon with more details.

Best regards,
[Your Email]
    """,  # Template 3: General reply
]

# Create FAISS index and add template embeddings
def initialize_faiss_index(templates):
    # Convert templates to embeddings (strip placeholders for cleaner embeddings)
    clean_templates = [re.sub(r'{sender_name}', '', template) for template in templates]
    template_embeddings = model.encode(clean_templates)
    # Create a FAISS index (using L2 distance)
    dimension = template_embeddings.shape[1]  # Embedding dimension
    index = faiss.IndexFlatL2(dimension)
    # Add embeddings to the index
    index.add(template_embeddings)
    return index, template_embeddings

# Initialize FAISS index at startup
faiss_index, template_embeddings = initialize_faiss_index(EMAIL_TEMPLATES)

def authenticate_gmail():
    """Authenticate with Gmail API and return the service object."""
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    
    return build('gmail', 'v1', credentials=creds)

def get_unread_emails(service):
    """Retrieve unread emails from the inbox."""
    results = service.users().messages().list(userId='me', labelIds=['INBOX'], q='is:unread').execute()
    messages = results.get('messages', [])
    return messages

def get_thread_details(service, thread_id):
    """Get all messages in a thread and extract details."""
    thread = service.users().threads().get(userId='me', id=thread_id).execute()
    messages = thread.get('messages', [])
    
    thread_details = []
    thread_content = ""  # To store concatenated content for context analysis
    for msg in messages:
        headers = msg['payload']['headers']
        subject = next((header['value'] for header in headers if header['name'] == 'Subject'), '')
        from_email = next((header['value'] for header in headers if header['name'] == 'From'), '')
        message_id = next((header['value'] for header in headers if header['name'] == 'Message-ID'), '')
        date = next((header['value'] for header in headers if header['name'] == 'Date'), '')
        
        # Extract sender's name from 'From' header
        sender_name = ''
        match = re.match(r'(.+?)\s*<\S+@[\S\.]+>', from_email)
        if match:
            sender_name = match.group(1).strip('"').strip()
        else:
            sender_name = from_email.split('@')[0]  # Fallback to email username if no name is found
        
        # Extract message body (simplified, assumes text/plain part)
        body = ''
        if 'parts' in msg['payload']:
            for part in msg['payload']['parts']:
                if part['mimeType'] == 'text/plain' and 'data' in part['body']:
                    body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='ignore')
                    break
        elif 'data' in msg['payload']['body']:
            body = base64.urlsafe_b64decode(msg['payload']['body']['data']).decode('utf-8', errors='ignore')
        
        thread_details.append({
            'from': from_email,
            'sender_name': sender_name,  # Add sender_name to thread details
            'subject': subject,
            'body': body,
            'message_id': message_id,
            'date': date
        })
        thread_content += body + " "  # Concatenate body for context
    
    return thread_details, messages[-1]['id'], thread_content.strip()

def select_template(thread_content):
    """Select the most relevant email template using FAISS."""
    # Convert thread content to embedding
    thread_embedding = model.encode([thread_content])
    # Search FAISS index for the closest template
    distances, indices = faiss_index.search(thread_embedding, 1)  # Get top 1 match
    best_template_idx = indices[0][0]
    return EMAIL_TEMPLATES[best_template_idx]

def create_draft_reply(service, to_email, subject, thread_id, latest_message_id, thread_details, reply_content, sender_name):
    """Create a draft reply for the email thread."""
    # Use the selected template as the reply content, replacing {sender_name}
    reply_content = reply_content.format(sender_name=sender_name)
    latest_message = thread_details[-1]
    message = MIMEText(reply_content + f"\n\n> On {latest_message['date']}, {latest_message['from']} wrote:\n> {latest_message['body']}")
    message['to'] = to_email
    message['subject'] = f"Re: {subject}"
    message['In-Reply-To'] = latest_message_id
    message['References'] = latest_message_id

    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    draft = {
        'message': {
            'raw': raw_message,
            'threadId': thread_id
        }
    }
    return service.users().drafts().create(userId='me', body=draft).execute()

def mark_email_as_read(service, msg_id):
    """Mark an email as read."""
    service.users().messages().modify(
        userId='me',
        id=msg_id,
        body={'removeLabelIds': ['UNREAD']}
    ).execute()

def main():
    """Main function to check emails, read threads, and create draft replies."""
    service = authenticate_gmail()

    while True:
        try:
            print("Checking for new unread emails...")
            messages = get_unread_emails(service)
            if not messages:
                print("No new unread emails found.")
            else:
                print(f"Found {len(messages)} unread emails.")
                for message in messages:
                    msg_id = message['id']
                    thread_id = message['threadId']
                    print(f"Processing email with message ID: {msg_id} in thread: {thread_id}")
                    
                    # Get thread details and content
                    thread_details, latest_message_id, thread_content = get_thread_details(service, thread_id)
                    if not thread_details:
                        print(f"No details found for thread {thread_id}. Skipping.")
                        continue
                    
                    # Use details from the latest message for the reply
                    latest_message = thread_details[-1]
                    to_email = latest_message['from']
                    subject = latest_message['subject']
                    sender_name = latest_message['sender_name']  # Get sender's name
                    
                    print(f"Thread contains {len(thread_details)} messages. Latest from: {to_email}, Name: {sender_name}, Subject: {subject}")
                    
                    # Select the most relevant template
                    reply_content = select_template(thread_content)
                    print(f"Selected template: {reply_content.splitlines()[1]}...")  # Print first line of template for logging
                    
                    # Create draft reply
                    draft = create_draft_reply(service, to_email, subject, thread_id, latest_message_id, thread_details, reply_content, sender_name)
                    print(f"Draft created for thread {thread_id}. Draft ID: {draft['id']}")
                    
                    # Mark the specific email as read
                    mark_email_as_read(service, msg_id)
                    print(f"Marked email {msg_id} as read.")
            
            # Wait before checking again
            time.sleep(60)  # Check every 60 seconds
        except Exception as e:
            print(f"An error occurred: {e}")
            time.sleep(60)  # Wait before retrying on error

if __name__ == '__main__':
    main()