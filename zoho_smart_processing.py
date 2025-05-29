import imaplib
import smtplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
import time
import logging
from datetime import datetime, timedelta
import threading
import queue

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ZohoSmartPollingProcessor:
    def __init__(self, email_address, password):
        self.email_address = email_address
        self.password = password
        self.imap_server = "imappro.zoho.com"
        self.smtp_server = "smtppro.zoho.com"
        self.imap_port = 993
        self.smtp_port = 587
        self.processed_emails = set()
        self.running = False
        self.last_email_time = datetime.now()
        self.current_interval = 60  # Start with 60 seconds
        self.min_interval = 10      # Minimum 10 seconds
        self.max_interval = 300     # Maximum 5 minutes
        self.email_queue = queue.Queue()
        
    def connect_imap(self):
        """Connect to IMAP server"""
        try:
            mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
            mail.login(self.email_address, self.password)
            return mail
        except Exception as e:
            logger.error(f"IMAP connection failed: {e}")
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
    
    def create_standard_reply(self, original_email):
        """Create a standard reply message"""
        reply_template = """
Thank you for your email. I have received your message and will get back to you as soon as possible.

This is an automated response. Please do not reply to this email.

Best regards,
Auto-Reply System

---
Original Message:
Subject: {subject}
From: {sender}
        """.strip()
        
        return reply_template.format(
            subject=original_email['subject'],
            sender=original_email['sender']
        )
    
    def save_reply_as_draft(self, original_email, reply_body):
        """Save reply as draft in Drafts folder"""
        try:
            reply = MIMEMultipart()
            reply['From'] = self.email_address
            reply['To'] = original_email['sender']
            reply['Subject'] = f"Re: {original_email['subject']}"
            
            if original_email['message_id']:
                reply['In-Reply-To'] = original_email['message_id']
                reply['References'] = original_email['message_id']
            
            reply.attach(MIMEText(reply_body, 'plain'))
            
            mail = self.connect_imap()
            if not mail:
                return False
            
            try:
                mail.select('Drafts')
            except:
                try:
                    mail.select('DRAFT')
                except:
                    logger.error("Could not find Drafts folder")
                    mail.close()
                    mail.logout()
                    return False
            
            mail.append('Drafts', '\\Draft', imaplib.Time2Internaldate(time.time()), 
                       reply.as_string().encode('utf-8'))
            
            mail.close()
            mail.logout()
            
            logger.info(f"Draft reply saved for email from {original_email['sender']}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving draft: {e}")
            return False
    
    def get_new_emails(self):
        """Fetch new emails since last check"""
        mail = self.connect_imap()
        if not mail:
            return []
        
        try:
            mail.select('INBOX')
            
            # Search for emails since last check (more efficient)
            since_date = (datetime.now() - timedelta(minutes=10)).strftime("%d-%b-%Y")
            status, messages = mail.search(None, f'(UNSEEN SINCE {since_date})')
            
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
    
    def adjust_polling_interval(self, found_emails):
        """Dynamically adjust polling interval based on email activity"""
        if found_emails:
            # New emails found - increase frequency (decrease interval)
            self.current_interval = max(self.min_interval, self.current_interval * 0.5)
            self.last_email_time = datetime.now()
            logger.info(f"New emails found - reducing interval to {self.current_interval} seconds")
        else:
            # No new emails - decrease frequency (increase interval)
            time_since_last_email = (datetime.now() - self.last_email_time).total_seconds()
            
            if time_since_last_email > 600:  # 10 minutes without emails
                self.current_interval = min(self.max_interval, self.current_interval * 1.2)
                logger.debug(f"No activity - increasing interval to {self.current_interval} seconds")
    
    def email_processor_worker(self):
        """Background worker to process emails from queue"""
        while self.running:
            try:
                email_data = self.email_queue.get(timeout=1)
                if email_data is None:  # Shutdown signal
                    break
                
                # Process email
                reply_body = self.create_standard_reply(email_data)
                if self.save_reply_as_draft(email_data, reply_body):
                    logger.info(f"Successfully processed email: {email_data['subject']}")
                else:
                    logger.error(f"Failed to process email: {email_data['subject']}")
                
                self.email_queue.task_done()
                
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error in email processor worker: {e}")
    
    def smart_polling_loop(self):
        """Main smart polling loop"""
        logger.info("Starting smart polling email monitor...")
        
        while self.running:
            try:
                start_time = time.time()
                
                # Check for new emails
                new_emails = self.get_new_emails()
                
                # Add new emails to processing queue
                for email_data in new_emails:
                    self.email_queue.put(email_data)
                
                # Adjust polling interval based on activity
                self.adjust_polling_interval(new_emails)
                
                # Calculate sleep time
                processing_time = time.time() - start_time
                sleep_time = max(1, self.current_interval - processing_time)
                
                logger.debug(f"Processed {len(new_emails)} emails in {processing_time:.2f}s. "
                           f"Sleeping for {sleep_time:.1f}s (interval: {self.current_interval}s)")
                
                time.sleep(sleep_time)
                
            except Exception as e:
                logger.error(f"Error in smart polling loop: {e}")
                time.sleep(30)  # Wait before retrying
    
    def start_monitoring(self):
        """Start the smart email monitoring"""
        self.running = True
        logger.info("Starting smart email monitoring system...")
        
        # Start email processor worker thread
        processor_thread = threading.Thread(target=self.email_processor_worker, daemon=True)
        processor_thread.start()
        
        # Start polling thread
        polling_thread = threading.Thread(target=self.smart_polling_loop, daemon=True)
        polling_thread.start()
        
        try:
            # Keep main thread alive
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Stopping email monitoring...")
            self.stop_monitoring()
    
    def stop_monitoring(self):
        """Stop the email monitoring"""
        self.running = False
        # Send shutdown signal to worker
        self.email_queue.put(None)
        logger.info("Email monitoring stopped")

# Usage example
if __name__ == "__main__":
    # Replace with your Zoho email credentials
    EMAIL_ADDRESS = "support@getagentstudio.com"
    PASSWORD = "AgenT@007Studi0DxB"  # Use app-specific password if 2FA is enabled
    
    # Create processor instance
    processor = ZohoSmartPollingProcessor(EMAIL_ADDRESS, PASSWORD)
    
    # Start smart monitoring
    try:
        processor.start_monitoring()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        processor.stop_monitoring()