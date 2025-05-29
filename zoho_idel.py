import imaplib
import smtplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
import time
import logging
import threading
import select
import socket
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ZohoEmailIdleProcessor:
    def __init__(self, email_address, password):
        self.email_address = email_address
        self.password = password
        self.imap_server = "imappro.zoho.com"
        self.smtp_server = "smtppro.zoho.com"
        self.imap_port = 993
        self.smtp_port = 587
        self.processed_emails = set()
        self.running = False
        self.mail = None
        
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
            draft_mail = self.connect_imap()
            if not draft_mail:
                return False
            
            # Select or create Drafts folder
            try:
                draft_mail.select('Drafts')
            except:
                try:
                    draft_mail.select('DRAFT')
                except:
                    logger.error("Could not find Drafts folder")
                    draft_mail.close()
                    draft_mail.logout()
                    return False
            
            # Save as draft
            draft_mail.append('Drafts', '\\Draft', imaplib.Time2Internaldate(time.time()), 
                            reply.as_string().encode('utf-8'))
            
            draft_mail.close()
            draft_mail.logout()
            
            logger.info(f"Draft reply saved for email from {original_email['sender']}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving draft: {e}")
            return False
    
    def process_new_email(self, email_id):
        """Process a single new email"""
        try:
            # Fetch email
            status, msg_data = self.mail.fetch(email_id, '(RFC822)')
            if status != 'OK':
                return
            
            # Parse email
            raw_email = msg_data[0][1]
            email_message = email.message_from_bytes(raw_email)
            
            # Extract email details
            subject = self.decode_header_value(email_message.get('Subject', ''))
            sender = self.decode_header_value(email_message.get('From', ''))
            message_id = email_message.get('Message-ID', '')
            
            # Get email body
            body = self.extract_body(email_message)
            
            email_data = {
                'id': email_id.decode(),
                'subject': subject,
                'sender': sender,
                'body': body,
                'message_id': message_id,
                'email_object': email_message
            }
            
            logger.info(f"Processing new email from {sender}: {subject}")
            
            # Create and save reply
            reply_body = self.create_standard_reply(email_data)
            self.save_reply_as_draft(email_data, reply_body)
            
        except Exception as e:
            logger.error(f"Error processing email {email_id}: {e}")
    
    def idle_loop(self):
        """Main IDLE loop for real-time email monitoring"""
        while self.running:
            try:
                # Connect to IMAP
                self.mail = self.connect_imap()
                if not self.mail:
                    logger.error("Failed to connect to IMAP server")
                    time.sleep(30)
                    continue
                
                # Select INBOX
                self.mail.select('INBOX')
                
                logger.info("Starting IMAP IDLE mode - waiting for new emails...")
                
                while self.running:
                    # Start IDLE
                    tag = self.mail._new_tag()
                    self.mail.send(f'{tag} IDLE\r\n'.encode())
                    
                    # Wait for response or timeout
                    response = self.mail.readline()
                    if b'+ idling' in response.lower():
                        logger.info("IDLE mode activated - monitoring for new emails...")
                        
                        # Wait for notifications
                        while self.running:
                            try:
                                # Use select to wait for data with timeout
                                ready, _, _ = select.select([self.mail.sock], [], [], 30)
                                
                                if ready:
                                    response = self.mail.readline()
                                    logger.debug(f"IDLE response: {response}")
                                    
                                    # Check if it's a new email notification
                                    if b'EXISTS' in response:
                                        logger.info("New email detected!")
                                        
                                        # Exit IDLE mode
                                        self.mail.send(b'DONE\r\n')
                                        self.mail.readline()  # Read the completion response
                                        
                                        # Process new emails
                                        self.process_recent_emails()
                                        break
                                        
                                else:
                                    # Timeout - send NOOP to keep connection alive
                                    logger.debug("IDLE timeout - sending NOOP")
                                    self.mail.send(b'DONE\r\n')
                                    self.mail.readline()
                                    break
                                    
                            except socket.timeout:
                                logger.debug("Socket timeout in IDLE")
                                break
                            except Exception as e:
                                logger.error(f"Error in IDLE loop: {e}")
                                break
                    else:
                        logger.warning(f"IDLE not supported or failed: {response}")
                        # Fallback to polling
                        self.fallback_polling()
                        break
                        
            except Exception as e:
                logger.error(f"Error in IDLE loop: {e}")
                if self.mail:
                    try:
                        self.mail.close()
                        self.mail.logout()
                    except:
                        pass
                time.sleep(30)  # Wait before reconnecting
    
    def process_recent_emails(self):
        """Process recent unread emails"""
        try:
            # Search for unread emails
            status, messages = self.mail.search(None, 'UNSEEN')
            if status != 'OK':
                return
                
            email_ids = messages[0].split()
            
            for email_id in email_ids:
                if email_id.decode() not in self.processed_emails:
                    self.process_new_email(email_id)
                    self.processed_emails.add(email_id.decode())
                    
        except Exception as e:
            logger.error(f"Error processing recent emails: {e}")
    
    def fallback_polling(self):
        """Fallback to polling if IDLE is not supported"""
        logger.info("IDLE not supported, falling back to polling every 30 seconds")
        
        while self.running:
            try:
                self.process_recent_emails()
                time.sleep(30)
            except Exception as e:
                logger.error(f"Error in polling fallback: {e}")
                time.sleep(30)
    
    def start_monitoring(self):
        """Start the email monitoring"""
        self.running = True
        logger.info("Starting real-time email monitoring...")
        
        # Start IDLE loop in a separate thread
        idle_thread = threading.Thread(target=self.idle_loop, daemon=True)
        idle_thread.start()
        
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
        if self.mail:
            try:
                self.mail.send(b'DONE\r\n')  # Exit IDLE mode
                self.mail.close()
                self.mail.logout()
            except:
                pass

# Usage example
if __name__ == "__main__":
    # Replace with your Zoho email credentials
    EMAIL_ADDRESS = "support@getagentstudio.com"
    PASSWORD = "AgenT@007Studi0DxB"  # Use app-specific password if 2FA is enabled
    
    # Create processor instance
    processor = ZohoEmailIdleProcessor(EMAIL_ADDRESS, PASSWORD)
    
    # Start real-time monitoring
    try:
        processor.start_monitoring()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        processor.stop_monitoring()