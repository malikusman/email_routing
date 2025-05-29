import os
import time
import base64
from email.mime.text import MIMEText
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
import email.utils

# Gmail API scope for reading emails and creating drafts
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

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
    for msg in messages:
        headers = msg['payload']['headers']
        subject = next((header['value'] for header in headers if header['name'] == 'Subject'), '')
        from_email = next((header['value'] for header in headers if header['name'] == 'From'), '')
        message_id = next((header['value'] for header in headers if header['name'] == 'Message-ID'), '')
        date = next((header['value'] for header in headers if header['name'] == 'Date'), '')
        
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
            'subject': subject,
            'body': body,
            'message_id': message_id,
            'date': date
        })
    
    return thread_details, messages[-1]['id']  # Return thread details and latest message ID

def create_draft_reply(service, to_email, subject, thread_id, latest_message_id, thread_details):
    """Create a draft reply for the email thread."""
    # Create reply content, quoting the latest message
    latest_message = thread_details[-1]
    reply_content = f"""
Thank you for your email. I'll get back to you soon with more details.

Best regards,
[Your Name]

> On {latest_message['date']}, {latest_message['from']} wrote:
> {latest_message['body']}
"""

    message = MIMEText(reply_content)
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
                    
                    # Get thread details
                    thread_details, latest_message_id = get_thread_details(service, thread_id)
                    if not thread_details:
                        print(f"No details found for thread {thread_id}. Skipping.")
                        continue
                    
                    # Use details from the latest message for the reply
                    latest_message = thread_details[-1]
                    to_email = latest_message['from']
                    subject = latest_message['subject']
                    
                    print(f"Thread contains {len(thread_details)} messages. Latest from: {to_email}, Subject: {subject}")
                    
                    # Create draft reply
                    draft = create_draft_reply(service, to_email, subject, thread_id, latest_message_id, thread_details)
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