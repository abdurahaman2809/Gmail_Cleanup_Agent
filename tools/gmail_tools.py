from langchain.tools import tool
import email
from email.header import decode_header
import os
import time

from dotenv import load_dotenv
load_dotenv()

EMAIL_ACCOUNT = os.getenv("EMAIL_ACCOUNT")
batch_size = int(os.getenv("limit", "5"))
# Global connection placeholder (will be set in main)
imap_client = None

def set_imap_client(client):
    global imap_client
    imap_client = client

def clean_text(text):
    """Cleans up the text from the email body."""
    if not text:
        return ""
    return "".join(text.split())[:100]

def get_email_body_content(msg):
    """Extracts plain text body from email."""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            if content_type == "text/plain" and "attachment" not in content_disposition:
                try:
                    return part.get_payload(decode=True).decode()
                except:
                    pass
    else:
        try:
            return msg.get_payload(decode=True).decode()
        except:
            pass
    return ""

def decode_mime_words(s):
    """Decodes MIME-encoded headers."""
    if not s: return ""
    return u''.join(
        word.decode(encoding or 'utf8') if isinstance(word, bytes) else word
        for word, encoding in decode_header(s))

@tool
def search_emails(keyword: str) -> str:
    """
    Searches for emails where the keyword appears in the Subject or Sender.
    Returns a list of matching emails with UIDs.
    Args:
        keyword: The string to search for.
    """
    time.sleep(21) # Rate Limit Handling for OpenAI Free Tier. Comment out if using paid tier.
    print(f"\n[TOOL] search_emails called with keyword='{keyword}'")
    global imap_client
    if not imap_client:
        return "Error: Not connected to Gmail."
    
    try:
        imap_client.select("INBOX")
        safe_keyword = keyword.replace('"', '').replace("'", "")
        
        criteria = f'TEXT "{safe_keyword}"'
        
        print(f"[DEBUG] Searching INBOX with criteria: {criteria}")
        status, messages = imap_client.uid('SEARCH', criteria)
        
        if not messages or messages[0] is None or len(messages[0]) == 0:
            print(f"No match in INBOX. Trying '[Gmail]/All Mail'...")
            try:
                imap_client.select('"[Gmail]/All Mail"')
                # Guardrail for prevention of deletion from sent items. Exclude Sent items by filtering OUT emails from me
                if EMAIL_ACCOUNT:
                    criteria = f'(TEXT "{safe_keyword}" NOT FROM "{EMAIL_ACCOUNT}")'
                    print(f"[DEBUG] Searching All Mail (excluding Sent) with: {criteria}")
                else:
                    print("[DEBUG] EMAIL_ACCOUNT not set, searching for text only...")
                
                status, messages = imap_client.uid('SEARCH', criteria)
            except Exception as e:
                print(f"Could not search All Mail: {e}")

        if not messages or messages[0] is None or len(messages[0]) == 0:
            return f"No emails found matching '{keyword}'."
        
        email_ids = messages[0].split()
        total_found = len(email_ids)
        
        # Return only the most recent 10 matches to save context window
        recent_ids = email_ids[-10:]
        
        results = []
        for e_id in reversed(recent_ids):
            _, msg_data = imap_client.uid('FETCH', e_id, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            
            subject = decode_mime_words(msg["Subject"])
            sender = decode_mime_words(msg["From"])
            body = clean_text(get_email_body_content(msg))
            
            results.append(f"ID: {e_id.decode()} | From: {sender} | Subject: {subject} | Body: {body}")
            
        return f"Found {total_found} matches. Showing the most recent {len(results)}:\n\n" + "\n\n".join(results)
        
    except Exception as e:
        return f"Error searching emails: {e}"

@tool
def fetch_recent_emails(batch_size: int, page: int = 1) -> str:
    """
    Fetches emails from the inbox in batches using UIDs.
    Returns a formatted string list of emails with ID, Sender, Subject, and Body preview.
    Args:
        batch_size: Number of emails per batch (default: 10)
        page: Which page/batch to fetch (1 = most recent 10, 2 = next 10, etc.)
    """
    time.sleep(21) #Rate Limit Handling for OpenAI Free Tier. Comment out if using paid tier.
    print(f"\n[TOOL] fetch_recent_emails called with batch_size={batch_size}, page={page}")
    global imap_client
    if not imap_client:
        return "Error: Not connected to Gmail."

    try:
        imap_client.select('"[Gmail]/All Mail"')
        
        # Search ALL emails but EXCLUDE sent items
        criteria = "ALL"
        if EMAIL_ACCOUNT:
            criteria = f'(NOT FROM "{EMAIL_ACCOUNT}")'
            
        print(f"[DEBUG] Fetching recent emails from All Mail with criteria: {criteria}")
        status, messages = imap_client.uid('SEARCH', criteria)
        
        if not messages or messages[0] is None:
            return "Mailbox is empty."
        
        email_ids = messages[0].split()
        total_count = len(email_ids)
        total_pages = (total_count + batch_size - 1) // batch_size  # Ceiling division
        
        if page > total_pages:
            return f"No more emails. Total pages: {total_pages}"
        
        # Calculate slice indices (from end, since newest are last)
        end_idx = total_count - (page - 1) * batch_size
        start_idx = max(0, end_idx - batch_size)
        
        ids_to_fetch = email_ids[start_idx:end_idx]
        
        results = []
        for e_id in reversed(ids_to_fetch):  # Newest first within batch
            # USE UID FETCH
            _, msg_data = imap_client.uid('FETCH', e_id, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            
            subject = decode_mime_words(msg["Subject"])
            sender = decode_mime_words(msg["From"])
            body = clean_text(get_email_body_content(msg))
            
            results.append(f"ID: {e_id.decode()} | From: {sender} | Subject: {subject} | Body: {body}")
        
        header = f"Page {page}/{total_pages} | Total emails: {total_count} | Showing {len(results)} emails:\n\n"
        return header + "\n\n".join(results)
    except Exception as e:
        return f"Error fetching emails: {e}"

@tool
def delete_emails_by_ids(email_ids: list[str]) -> str:
    """
    Deletes multiple emails by their UIDs by moving them to [Gmail]/Trash.
    Works for both specific IDs and bulk lists.
    Args:
        email_ids: List of email UIDs to delete.
    """

    time.sleep(21) #Rate Limit Handling for OpenAI Free Tier. Comment out if using paid tier.
    print(f"\n[TOOL] delete_emails_by_ids called with {len(email_ids)} emails: {email_ids}")
    global imap_client
    if not imap_client:
        return "Error: Not connected to Gmail."
    
    if not email_ids:
        return "No email IDs provided."

    try:
        if imap_client.state != 'SELECTED':
             # Default to All Mail as it contains everything
             imap_client.select('"[Gmail]/All Mail"')

        deleted_ids = []
        failed_ids = []
        
        # Gmail "Trash" folder name
        trash_folder = '"[Gmail]/Trash"'

        for email_id in email_ids:
            try:
                # Method 1: MOVE to Trash (Copy to Trash -> Mark Deleted in Source)
                
                res_copy = imap_client.uid('COPY', email_id, trash_folder)
                
                if res_copy[0] == 'OK':
                    # 2. Use UID STORE to Mark as Deleted in current folder (to remove from here)
                    imap_client.uid('STORE', email_id, '+FLAGS', '\\Deleted')
                    deleted_ids.append(email_id)
                    print(f"[DEBUG] Moved UID {email_id} to Trash")
                else:
                    # Fallback: Just try marking deleted if Copy fails
                    imap_client.uid('STORE', email_id, '+FLAGS', '\\Deleted')
                    print(f"[DEBUG] Could not copy to Trash, forced delete flag on UID {email_id}")
                    
            except Exception as e:
                print(f"[DEBUG] Failed to process UID {email_id}: {e}")
                failed_ids.append(email_id)
        
        # Expunge to finalize cleanup in current folder
        if deleted_ids:
            imap_client.expunge()
            print(f"[DEBUG] Expunged {len(deleted_ids)} emails")
        
        result_msg = f"Successfully moved {len(deleted_ids)} emails to Trash: {deleted_ids}"
        if failed_ids:
             result_msg += f"\nFailed to process {len(failed_ids)} emails: {failed_ids}"
        return result_msg
        
    except Exception as e:
        print(f"[DEBUG] Batch delete error: {e}")
        return f"Error during batch delete: {e}"
