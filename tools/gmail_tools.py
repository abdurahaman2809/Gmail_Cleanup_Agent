from langchain.tools import tool
import email
from email.header import decode_header
import os
import time
import traceback

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
    start = time.perf_counter()
    print(f"\n[TOOL] search_emails called with keyword='{keyword}'")
    global imap_client
    if not imap_client:
        return "Error: Not connected to Gmail."
    
    try:
        print("[DEBUG] Selecting INBOX...")
        sel_res = imap_client.select("INBOX")
        print(f"[DEBUG] select INBOX result: {sel_res}")
        safe_keyword = keyword.replace('"', '').replace("'", "")

        criteria = f'TEXT "{safe_keyword}"'

        print(f"[Gmail Agent🤖] Searching INBOX with criteria: {criteria}")
        t0 = time.perf_counter()
        status, messages = imap_client.uid('SEARCH', criteria)
        t1 = time.perf_counter()
        print(f"[DEBUG] INBOX SEARCH took {t1-t0:.3f}s, status={status}")
        
        if not messages or messages[0] is None or len(messages[0]) == 0:
            print(f"No match in INBOX. Trying '[Gmail]/All Mail'...")
            try:
                print('[DEBUG] Selecting "[Gmail]/All Mail"...')
                sel_all = imap_client.select('"[Gmail]/All Mail"')
                print(f"[DEBUG] select All Mail result: {sel_all}")
                # Guardrail for prevention of deletion from sent items. Exclude Sent items by filtering OUT emails from me
                if EMAIL_ACCOUNT:
                    criteria = f'(TEXT "{safe_keyword}" NOT FROM "{EMAIL_ACCOUNT}")'
                    print(f"[Gmail Agent🤖] Searching All Mail (excluding Sent) with: {criteria}")
                else:
                    print("[Gmail Agent🤖] EMAIL_ACCOUNT not set, searching for text only...")
                t2 = time.perf_counter()
                status, messages = imap_client.uid('SEARCH', criteria)
                t3 = time.perf_counter()
                print(f"[DEBUG] All Mail SEARCH took {t3-t2:.3f}s, status={status}")
            except Exception as e:
                print(f"Could not search All Mail: {e}")
                traceback.print_exc()

        if not messages or messages[0] is None or len(messages[0]) == 0:
            return f"No emails found matching '{keyword}'."
        
        email_ids = messages[0].split()
        total_found = len(email_ids)
        
        # Return only the most recent 10 matches to save context window
        recent_ids = email_ids[-10:]
        
        results = []
        for e_id in reversed(recent_ids):
            fetch_t0 = time.perf_counter()
            fetch_res = imap_client.uid('FETCH', e_id, "(RFC822)")
            fetch_t1 = time.perf_counter()
            try:
                print(f"[DEBUG] FETCH UID {e_id} took {fetch_t1-fetch_t0:.3f}s, res={fetch_res[0]}")
            except Exception:
                print(f"[DEBUG] FETCH UID {e_id} raw res: {fetch_res}")
            _, msg_data = fetch_res
            msg = email.message_from_bytes(msg_data[0][1])

            subject = decode_mime_words(msg["Subject"])
            sender = decode_mime_words(msg["From"])
            body = clean_text(get_email_body_content(msg))

            results.append(f"ID: {e_id.decode()} | From: {sender} | Subject: {subject} | Body: {body}")
            
        duration = time.perf_counter() - start
        print(f"[TOOL] search_emails finished in {duration:.3f}s")
        return f"Found {total_found} matches. Showing the most recent {len(results)}:\n\n" + "\n\n".join(results)

    except Exception as e:
        print("[ERROR] Exception in search_emails:")
        traceback.print_exc()
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

    start = time.perf_counter()
    print(f"\n[TOOL] fetch_recent_emails called with batch_size={batch_size}, page={page}")
    global imap_client
    if not imap_client:
        return "Error: Not connected to Gmail."

    try:
        print('[DEBUG] Selecting "[Gmail]/All Mail" for fetch...')
        sel_res = imap_client.select('"[Gmail]/All Mail"')
        print(f"[DEBUG] select result: {sel_res}")

        # Search ALL emails but EXCLUDE sent items
        criteria = "ALL"
        if EMAIL_ACCOUNT:
            criteria = f'(NOT FROM "{EMAIL_ACCOUNT}")'

        print(f"[Gmail Agent🤖] Fetching recent emails from All Mail with criteria: {criteria}")
        t0 = time.perf_counter()
        status, messages = imap_client.uid('SEARCH', criteria)
        t1 = time.perf_counter()
        print(f"[DEBUG] SEARCH took {t1-t0:.3f}s, status={status}")
        
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
            fetch_t0 = time.perf_counter()
            fetch_res = imap_client.uid('FETCH', e_id, "(RFC822)")
            fetch_t1 = time.perf_counter()
            try:
                print(f"[DEBUG] FETCH UID {e_id} took {fetch_t1-fetch_t0:.3f}s, res={fetch_res[0]}")
            except Exception:
                print(f"[DEBUG] FETCH UID {e_id} raw res: {fetch_res}")
            _, msg_data = fetch_res
            msg = email.message_from_bytes(msg_data[0][1])

            subject = decode_mime_words(msg["Subject"])
            sender = decode_mime_words(msg["From"])
            body = clean_text(get_email_body_content(msg))

            results.append(f"ID: {e_id.decode()} | From: {sender} | Subject: {subject} | Body: {body}")
        
        header = f"Page {page}/{total_pages} | Total emails: {total_count} | Showing {len(results)} emails:\n\n"
        duration = time.perf_counter() - start
        print(f"[TOOL] fetch_recent_emails finished in {duration:.3f}s")
        return header + "\n\n".join(results)
    except Exception as e:
        print("[ERROR] Exception in fetch_recent_emails:")
        traceback.print_exc()
        return f"Error fetching emails: {e}"

@tool
def delete_emails_by_ids(email_ids: list[str]) -> str:
    """
    Deletes multiple emails by their UIDs by moving them to [Gmail]/Trash.
    Works for both specific IDs and bulk lists.
    Args:
        email_ids: List of email UIDs to delete.
    """
    start = time.perf_counter()
    print(f"\n[TOOL] delete_emails_by_ids called with {len(email_ids)} emails: {email_ids}")
    global imap_client
    if not imap_client:
        return "Error: Not connected to Gmail."
    
    if not email_ids:
        return "No email IDs provided."

    try:
        print(f"[DEBUG] imap_client state before delete: {getattr(imap_client, 'state', None)}")
        if imap_client.state != 'SELECTED':
             # Default to All Mail as it contains everything
             sel = imap_client.select('"[Gmail]/All Mail"')
             print(f"[DEBUG] select All Mail result: {sel}")

        deleted_ids = []
        failed_ids = []
        
        # Gmail "Trash" folder name
        trash_folder = '"[Gmail]/Trash"'

        for email_id in email_ids:
            try:
                print(f"[DEBUG] Processing UID {email_id} -> COPY to Trash")
                cp_t0 = time.perf_counter()
                res_copy = imap_client.uid('COPY', email_id, trash_folder)
                cp_t1 = time.perf_counter()
                print(f"[DEBUG] COPY UID {email_id} took {cp_t1-cp_t0:.3f}s, res={res_copy}")
                
                if res_copy and res_copy[0] == 'OK':
                    st_t0 = time.perf_counter()
                    st_res = imap_client.uid('STORE', email_id, '+FLAGS', '\\Deleted')
                    st_t1 = time.perf_counter()
                    print(f"[DEBUG] STORE UID {email_id} took {st_t1-st_t0:.3f}s, res={st_res}")
                    deleted_ids.append(email_id)
                    print(f"[Gmail Agent🤖] Moved UID {email_id} to Trash")
                else:
                    # Fallback: Just try marking deleted if Copy fails
                    print(f"[WARN] COPY failed for UID {email_id}, attempting STORE only")
                    st_res = imap_client.uid('STORE', email_id, '+FLAGS', '\\Deleted')
                    print(f"[DEBUG] STORE fallback res for {email_id}: {st_res}")
                    deleted_ids.append(email_id)
                    print(f"[Gmail Agent🤖] Forced delete flag on UID {email_id}")
                    
            except Exception as e:
                print(f"[Gmail Agent🤖] Failed to process UID {email_id}: {e}")
                traceback.print_exc()
                failed_ids.append(email_id)
        
        # Expunge to finalize cleanup in current folder
        if deleted_ids:
            print(f"[DEBUG] Expunging {len(deleted_ids)} emails...")
            exp_t0 = time.perf_counter()
            exp_res = imap_client.expunge()
            exp_t1 = time.perf_counter()
            print(f"[DEBUG] expunge took {exp_t1-exp_t0:.3f}s, res={exp_res}")
            print(f"[Gmail Agent🤖] Expunged {len(deleted_ids)} emails")
        
        result_msg = f"Successfully moved {len(deleted_ids)} emails to Trash: {deleted_ids}"
        if failed_ids:
             result_msg += f"\nFailed to process {len(failed_ids)} emails: {failed_ids}"
        duration = time.perf_counter() - start
        print(f"[TOOL] delete_emails_by_ids finished in {duration:.3f}s")
        return result_msg
        
    except Exception as e:
        print(f"[Gmail Agent🤖] Batch delete error: {e}")
        traceback.print_exc()
        return f"Error during batch delete: {e}"
