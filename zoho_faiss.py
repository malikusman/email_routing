import imaplib
import smtplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
import time
import logging
from datetime import datetime
import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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

class ZohoEmailProcessor:
    def __init__(self, email_address, password):
        self.email_address = email_address
        self.password = password
        self.imap_server = "imap.zoho.com"
        self.smtp_server = "smtp.zoho.com"
        self.imap_port = 993
        self.smtp_port = 587
        self.processed_emails = set()  # Track processed emails to avoid duplicates
        
    def connect_imap(self):
        """Connect to IMAP server"""
        try:
            mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
            mail.login(self.email_address, self.password)
            return mail
        except Exception as e:
            logger.error(f"IMAP connection failed: {e}")
            return None
    
    def connect_smtp(self):
        """Connect to SMTP server"""
        try:
            smtp = smtplib.SMTP(self.smtp_server, self.smtp_port)
            smtp.starttls()
            smtp.login(self.email_address, self.password)
            return smtp
        except Exception as e:
            logger.error(f"SMTP connection failed: {e}")
            return None
    
    def decode_header_value(self, header):
        """Decode email header"""
        decoded = decode_header(header)
        header_value = ""
        for part, encoding in decoded:
            if isinstance(part, bytes):
                header_value += part.decode(encoding or 'utf-8')
            else:
                header_value += str(part)
        return header_value
    
    def get_unread_emails(self):
        """Fetch unread emails from inbox"""
        mail = self.connect_imap()
        if not mail:
            return []
        
        try:
            mail.select('INBOX')
            # Search for unread emails
            status, messages = mail.search(None, 'UNSEEN')
            
            if status != 'OK':
                logger.error("Failed to search for emails")
                return []
            
            email_ids = messages[0].split()
            emails = []
            
            for email_id in email_ids:
                if email_id.decode() in self.processed_emails:
                    continue
                    
                # Fetch email
                status, msg_data = mail.fetch(email_id, '(RFC822)')
                if status != 'OK':
                    continue
                
                # Parse email
                raw_email = msg_data[0][1]
                email_message = email.message_from_bytes(raw_email)
                
                # Extract email details
                subject = self.decode_header_value(email_message.get('Subject', ''))
                sender = self.decode_header_value(email_message.get('From', ''))
                message_id = email_message.get('Message-ID', '')
                
                # Get email body
                body = self.extract_body(email_message)
                
                emails.append({
                    'id': email_id.decode(),
                    'subject': subject,
                    'sender': sender,
                    'body': body,
                    'message_id': message_id,
                    'email_object': email_message
                })
                
                self.processed_emails.add(email_id.decode())
                logger.info(f"Found new email from {sender}: {subject}")
            
            mail.close()
            mail.logout()
            return emails
            
        except Exception as e:
            logger.error(f"Error fetching emails: {e}")
            return []
    
    def extract_body(self, email_message):
        """Extract email body text"""
        body = ""
        
        if email_message.is_multipart():
            for part in email_message.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                
                if content_type == "text/plain" and "attachment" not in content_disposition:
                    try:
                        body = part.get_payload(decode=True).decode('utf-8')
                        break
                    except:
                        continue
        else:
            try:
                body = email_message.get_payload(decode=True).decode('utf-8')
            except:
                body = str(email_message.get_payload())
        
        return body
    
    def select_template(self, email_body):
        """Select the most relevant email template using FAISS."""
        if not email_body.strip():  # Handle empty email body
            logger.warning("Email body is empty, selecting default template")
            return EMAIL_TEMPLATES[2]  # Default to general reply
        
        thread_embedding = model.encode([email_body])
        distances, indices = faiss_index.search(thread_embedding, 1)
        best_template_idx = indices[0][0]
        logger.info(f"Selected template index {best_template_idx} with distance {distances[0][0]}")
        return EMAIL_TEMPLATES[best_template_idx]
    
    def create_standard_reply(self, original_email):
        """Create a reply message using FAISS to select the template"""
        # Select the most appropriate template using FAISS
        reply_template = self.select_template(original_email['body'])
        
        # Format the reply with original email details
        reply_body = reply_template + f"\n\n---\nOriginal Message:\nSubject: {original_email['subject']}\nFrom: {original_email['sender']}"
        return reply_body
    
    def save_reply_as_draft(self, original_email, reply_body):
        """Save reply as draft in Drafts folder"""
        try:
            # Create reply message
            reply = MIMEMultipart()
            reply['From'] = self.email_address
            reply['To'] = original_email['sender']
            reply['Subject'] = f"Re: {original_email['subject']}"
            
            # Add In-Reply-To and References headers for proper threading
            if original_email['message_id']:
                reply['In-Reply-To'] = original_email['message_id']
                reply['References'] = original_email['message_id']
            
            # Add reply body
            reply.attach(MIMEText(reply_body, 'plain'))
            
            # Connect to IMAP to save draft
            mail = self.connect_imap()
            if not mail:
                return False
            
            # Select or create Drafts folder
            try:
                mail.select('Drafts')
            except:
                # If Drafts folder doesn't exist, try DRAFT
                try:
                    mail.select('DRAFT')
                except:
                    logger.error("Could not find Drafts folder")
                    return False
            
            # Save as draft
            mail.append('Drafts', '\\Draft', imaplib.Time2Internaldate(time.time()), 
                       reply.as_string().encode('utf-8'))
            
            mail.close()
            mail.logout()
            
            logger.info(f"Draft reply saved for email from {original_email['sender']}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving draft: {e}")
            return False
    
    def process_emails(self):
        """Main method to process new emails"""
        logger.info("Checking for new emails...")
        
        # Get unread emails
        emails = self.get_unread_emails()
        
        if not emails:
            logger.info("No new emails found")
            return
        
        # Process each email
        for email_data in emails:
            logger.info(f"Processing email from {email_data['sender']}")
            
            # Create standard reply using FAISS
            reply_body = self.create_standard_reply(email_data)
            
            # Save reply as draft
            if self.save_reply_as_draft(email_data, reply_body):
                logger.info(f"Successfully processed email: {email_data['subject']}")
            else:
                logger.error(f"Failed to process email: {email_data['subject']}")
    
    def run_continuous(self, check_interval=60):
        """Run continuously, checking for new emails at specified intervals"""
        logger.info(f"Starting continuous monitoring (checking every {check_interval} seconds)")
        
        while True:
            try:
                self.process_emails()
                logger.info(f"Waiting {check_interval} seconds before next check...")
                time.sleep(check_interval)
            except KeyboardInterrupt:
                logger.info("Stopping email processor...")
                break
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                time.sleep(30)  # Wait 30 seconds before retrying

# Usage example
if __name__ == "__main__":
    # Replace with your Zoho email credentials
    EMAIL_ADDRESS = "support@getagentstudio.com"
    PASSWORD = "AgenT@007Studi0DxB"  # Use app-specific password if 2FA is enabled
    
    # Create processor instance
    processor = ZohoEmailProcessor(EMAIL_ADDRESS, PASSWORD)
    
    # Run continuously (check every 60 seconds)
    processor.run_continuous(check_interval=60)