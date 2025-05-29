import os
import time
import base64
import faiss
import numpy as np
from email.mime.text import MIMEText
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request  # Added missing import
from zoho_api_client import ZohoAPIClient  # Custom client for Zoho Mail API
from sentence_transformers import SentenceTransformer

# Zoho Mail API scope
SCOPES = ['ZohoMail.messages.ALL']

# Initialize the sentence transformer model for embeddings
model = SentenceTransformer('all-MiniLM-L6-v2')

# Define email templates
EMAIL_TEMPLATES = [
    """
Thank you for your email regarding

We will schedule a meeting at your earliest convenience to discuss this further.

Best regards,
[Your Name]
    """,  # Template 1: Meeting request
    """
Thank you for reaching out with your support inquiry. We have received your request and will respond within 24-48 hours.

Best regards,
[Your Name]
    """,  # Template 2: Support inquiry
    """
Thank you for your email. I'll get back to you soon with more details.

Best regards,
[Your Name]
    """,  # Template 3: General reply
]

# Create FAISS index and add template embeddings
def initialize_faiss_index(templates):
    template_embeddings = model.encode(templates)
    dimension = template_embeddings.shape[1]
    index = faiss.IndexFlatL2(dimension)
    index.add(template_embeddings)
    return index, template_embeddings

# Initialize FAISS index at startup
faiss_index, template_embeddings = initialize_faiss_index(EMAIL_TEMPLATES)

def authenticate_zoho():
    """Authenticate with Zoho Mail API and return the service object."""
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'zoho_credentials.json', SCOPES)
            creds = flow.run_local_server(port=8080)  # Fixed port for Zoho
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    
    return ZohoAPIClient(creds)

def get_unread_emails(service):
    """Retrieve unread emails from the inbox."""
    results = service.list_messages(folder='Inbox', unread=True)
    return results.get('data', []) if results else []

def get_thread_details(service, thread_id):
    """Get all messages in a thread and extract details."""
    thread = service.get_thread(thread_id)
    messages = thread.get('messages', [])
    
    thread_details = []
    thread_content = ""
    for msg in messages:
        headers = msg.get('headers', {})
        subject = headers.get('subject', '')
        from_email = headers.get('from', '')
        message_id = msg.get('message_id', '')
        date = headers.get('date', '')
        
        body = msg.get('content', '')  # Simplified for Zoho API structure
        thread_details.append({
            'from': from_email,
            'subject': subject,
            'body': body,
            'message_id': message_id,
            'date': date
        })
        thread_content += body + " "
    
    return thread_details, messages[-1]['message_id'], thread_content.strip()

def select_template(thread_content):
    """Select the most relevant email template using FAISS."""
    thread_embedding = model.encode([thread_content])
    distances, indices = faiss_index.search(thread_embedding, 1)
    best_template_idx = indices[0][0]
    return EMAIL_TEMPLATES[best_template_idx]

def create_draft_reply(service, to_email, subject, thread_id, latest_message_id, thread_details, reply_content):
    """Create a draft reply for the email thread."""
    latest_message = thread_details[-1]
    message = MIMEText(reply_content + f"\n\n> On {latest_message['date']}, {latest_message['from']} wrote:\n> {latest_message['body']}")
    message['to'] = to_email
    message['subject'] = f"Re: {subject}"
    message['In-Reply-To'] = latest_message_id
    message['References'] = latest_message_id

    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    draft = service.create_draft(to_email, subject, raw_message, thread_id)
    return draft

def mark_email_as_read(service, msg_id):
    """Mark an email as read."""
    service.update_message(msg_id, {'unread': False})

def main():
    """Main function to check emails, read threads, and create draft replies."""
    service = authenticate_zoho()

    while True:
        try:
            print("Checking for new unread emails...")
            messages = get_unread_emails(service)
            if not messages:
                print("No new unread emails found.")
            else:
                print(f"Found {len(messages)} unread emails.")
                for message in messages:
                    msg_id = message['message_id']
                    thread_id = message.get('thread_id', msg_id)  # Zoho may use message_id as thread_id
                    print(f"Processing email with message ID: {msg_id} in thread: {thread_id}")
                    
                    thread_details, latest_message_id, thread_content = get_thread_details(service, thread_id)
                    if not thread_details:
                        print(f"No details found for thread {thread_id}. Skipping.")
                        continue
                    
                    latest_message = thread_details[-1]
                    to_email = latest_message['from']
                    subject = latest_message['subject']
                    
                    print(f"Thread contains {len(thread_details)} messages. Latest from: {to_email}, Subject: {subject}")
                    
                    reply_content = select_template(thread_content)
                    print(f"Selected template: {reply_content.splitlines()[1]}...")
                    
                    draft = create_draft_reply(service, to_email, subject, thread_id, latest_message_id, thread_details, reply_content)
                    print(f"Draft created for thread {thread_id}. Draft ID: {draft['id']}")
                    
                    mark_email_as_read(service, msg_id)
                    print(f"Marked email {msg_id} as read.")
            
            time.sleep(60)  # Check every 60 seconds
        except Exception as e:
            print(f"An error occurred: {e}")
            time.sleep(60)

if __name__ == '__main__':
    main()