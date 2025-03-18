import email
import re
import os
import json
from email.header import decode_header
from elasticsearch import Elasticsearch
import argparse
from datetime import datetime

def decode_email_header(header):
    """Decode email header to readable format."""
    if header:
        decoded_header, encoding = decode_header(header)[0]
        if isinstance(decoded_header, bytes):
            # Handle unknown-8bit encoding by using utf-8 with replace error handling
            if encoding and encoding.lower() in ['unknown', 'unknown-8bit']:
                return decoded_header.decode('utf-8', errors='replace')
            return decoded_header.decode(encoding or 'utf-8', errors='replace')
        return decoded_header
    return ""

def extract_ip_addresses(text):
    """Extract IP addresses from text using regex."""
    if not text:
        return []
    ip_pattern = r'\b(?:\d{1,3}\.){3}\d{1,3}\b'
    return re.findall(ip_pattern, text)

def extract_urls(text):
    """Extract URLs from text using regex."""
    if not text:
        return []
    # Pattern for URL extraction
    url_pattern = r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+'
    return re.findall(url_pattern, text)

def extract_domains(text):
    """Extract domains from text and URLs."""
    if not text:
        return []
    # Extract domains from URLs
    urls = extract_urls(text)
    domains = []
    
    # Extract domains from URLs
    for url in urls:
        # Remove protocol (http://, https://)
        domain = re.sub(r'https?://', '', url)
        # Get domain part (before first slash)
        domain = domain.split('/')[0]
        if domain not in domains:
            domains.append(domain)
    
    # Also look for domains not in URLs
    domain_pattern = r'(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}'
    found_domains = re.findall(domain_pattern, text)
    
    # Add unique domains
    for domain in found_domains:
        if domain not in domains:
            domains.append(domain)
            
    return domains

def extract_iocs_from_email(email_file):
    """Extract IOCs from an email file."""
    try:
        with open(email_file, 'rb') as f:
            msg = email.message_from_binary_file(f)
        
        # Extract basic headers
        sender = decode_email_header(msg.get('From', ''))
        recipient = decode_email_header(msg.get('To', ''))
        subject = decode_email_header(msg.get('Subject', ''))
        
        # Extract sender email
        sender_email = re.findall(r'[\w\.-]+@[\w\.-]+', sender)
        sender_email = sender_email[0] if sender_email else ""
        
        # Extract recipient email
        recipient_email = re.findall(r'[\w\.-]+@[\w\.-]+', recipient)
        recipient_email = recipient_email[0] if recipient_email else ""
        
        # Extract IPs from headers
        received_headers = msg.get_all('Received', [])
        all_header_text = ' '.join(received_headers)
        
        sender_ips = extract_ip_addresses(all_header_text)
        # Often the first IP is the sender's IP
        sender_ip = sender_ips[0] if sender_ips else ""
        
        # Get email body
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain" or content_type == "text/html":
                    try:
                        payload = part.get_payload(decode=True)
                        charset = part.get_content_charset() or 'utf-8'
                        if charset and charset.lower() in ['unknown', 'unknown-8bit']:
                            charset = 'utf-8'
                        body += payload.decode(charset, errors='replace')
                    except Exception as e:
                        print(f"Error decoding part: {e}")
                        # Fallback to utf-8 with replace
                        try:
                            body += payload.decode('utf-8', errors='replace')
                        except:
                            pass
        else:
            try:
                payload = msg.get_payload(decode=True)
                charset = msg.get_content_charset() or 'utf-8'
                if charset and charset.lower() in ['unknown', 'unknown-8bit']:
                    charset = 'utf-8'
                body = payload.decode(charset, errors='replace')
            except Exception as e:
                print(f"Error decoding body: {e}")
                # Fallback to utf-8 with replace
                try:
                    body = payload.decode('utf-8', errors='replace')
                except:
                    pass
        
        # Extract URLs and domains from body
        urls = extract_urls(body)
        domains = extract_domains(body)
        
        # Create IOC dictionary
        iocs = {
            "timestamp": datetime.now().isoformat(),
            "file_name": os.path.basename(email_file),
            "sender": sender,
            "sender_email": sender_email,
            "sender_ip": sender_ip,
            "recipient": recipient,
            "recipient_email": recipient_email,
            "subject": subject,
            "urls": urls,
            "domains": domains
        }
        
        return iocs
    
    except Exception as e:
        print(f"Error processing {email_file}: {e}")
        # Return a minimal IOC object so the script can continue
        return {
            "timestamp": datetime.now().isoformat(),
            "file_name": os.path.basename(email_file),
            "error": str(e)
        }

def send_to_elastic(iocs, es_cloud_id, es_username, es_password, index_name):
    """Send IOCs to Elastic Cloud."""
    try:
        es = Elasticsearch(
            cloud_id=es_cloud_id,
            basic_auth=(es_username, es_password)
        )
        
        # Check if the connection is successful
        if not es.ping():
            print("Failed to connect to Elasticsearch")
            return False
        
        # Index the document
        response = es.index(
            index=index_name,
            document=iocs
        )
        
        print(f"Document indexed with ID: {response['_id']}")
        return True
    
    except Exception as e:
        print(f"Error sending to Elasticsearch: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description='Extract IOCs from email files and send to Elastic Cloud')
    parser.add_argument('--email-dir', required=True, help='Directory containing email files')
    parser.add_argument('--es-cloud-id', required=True, help='Elastic Cloud ID')
    parser.add_argument('--es-username', required=True, help='Elasticsearch username')
    parser.add_argument('--es-password', required=True, help='Elasticsearch password')
    parser.add_argument('--index-name', default='email-iocs', help='Elasticsearch index name')
    parser.add_argument('--output-file', default='iocs.json', help='Output JSON file')
    
    args = parser.parse_args()
    
    all_iocs = []
    
    # Process each email file in the directory
    for file in os.listdir(args.email_dir):
        if not file.endswith('.eml'):
            continue
        
        email_path = os.path.join(args.email_dir, file)
        print(f"Processing {email_path}...")
        
        iocs = extract_iocs_from_email(email_path)
        all_iocs.append(iocs)
        
        # Send to Elastic Cloud
        if args.es_cloud_id and args.es_username and args.es_password:
            send_to_elastic(
                iocs,
                args.es_cloud_id,
                args.es_username,
                args.es_password,
                args.index_name
            )
    
    # Save all IOCs to JSON file
    with open(args.output_file, 'w') as f:
        json.dump(all_iocs, f, indent=2)
    
    print(f"Extracted IOCs from {len(all_iocs)} emails and saved to {args.output_file}")

if __name__ == "__main__":
    main()
