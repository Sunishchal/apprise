import base64
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import tiktoken
import openai
import os
import requests
import datetime
from pyairtable import Table
import pandas as pd


def TableDF(table):
    columns = {}
    table_data = table.all()
    if len(table_data) > 0:
        # -- get columns
        columns = {}
        for col in table_data[0]["fields"].keys():
            columns[col] = []
    
        # -- populate dataframe
        df = pd.DataFrame(columns)
        for r, col in enumerate(table_data):
            entry = {}
            for col in table_data[r]["fields"].keys():
                entry[col] = table_data[r]["fields"][col]
            df = df.append(entry, ignore_index=True)
        return df
    else:
        return None

# Use the GPT tokenizer to figure out the word count of the input
def num_tokens_from_string(string: str, encoding_name: str) -> int:
    encoding = tiktoken.encoding_for_model(encoding_name)
    num_tokens = len(encoding.encode(string))
    return num_tokens

def run()
    # Retrieve API Keys & Gmail app password from env variables
    OPENAI_KEY = os.getenv('OPENAI_KEY')
    openai.api_key = OPENAI_KEY
    gpt_model = 'gpt-4'

    AIRTABLE_API_KEY = os.getenv('AIRTABLE_API_KEY')
    AIRTABLE_BASE_ID = 'appaIzcAYJHAUCqBM'
    subscriber = Table(AIRTABLE_API_KEY, AIRTABLE_BASE_ID, 'Subscriber')
    interest = Table(AIRTABLE_API_KEY, AIRTABLE_BASE_ID, 'Interest')

    GMAIL_PASS = os.getenv('GMAIL_PASS')
    from_email = 'federal.register.ai.summary@gmail.com'
    to_emails = []

    # Create variables for year, month, and date in ISO format
    # The register is published daily at 6am EST, so default to yesterday if it's too early in the day
    if datetime.datetime.now().hour < 11: # 11am UTC is 6am EST
        date = datetime.date.today() - datetime.timedelta(1)
    else:
        date = datetime.date.today()
        year = date.strftime("%Y")
        month = date.strftime("%m")
    day = date.strftime("%d")

    # Define the API endpoint to retrieve today's Federal Register documents
    url = f"https://www.federalregister.gov/api/v1/issues/{year}-{month}-{day}.json"

    # Make the API request
    response = requests.get(url)

    if str(response) == '<Response [404]>':
        print('No federal register today, exiting process.')
        return

    r = response.json()

    # Filter Airtable records to only active subscribers with a valid email address
    subs = TableDF(subscriber)
    subs = subs.query("Subscribed == 1")
    interests = TableDF(interest)

    for i, sub in subs.iterrows():
    to_email = sub['Subscriber Work Email']
    result = "-----------AI Summary--------\nPlease excuse any typos or anomalies, our AI is still learning :)\n\n"
    formatted_abstracts = ''

    for sub_interest in sub['Interests']:
        agencies = interests[interests['Interest'] == sub_interest]['Agency Name'].tolist()

        # Parse the document list to get all doc numbers for relevant agencies
        docs = [n for n in r['agencies'] if n['name'] in agencies]
        doc_numbers = [doc_num for entry in docs for doc_cat in entry["document_categories"] for doc in doc_cat["documents"] for doc_num in doc["document_numbers"]]

        abstracts = ''
        for doc_num in doc_numbers:
        # Define the API endpoint to retrieve a single document of interest
        url = f"https://www.federalregister.gov/api/v1/documents/{doc_num}.json?fields[]=abstract&fields[]=pdf_url&fields[]=title"

        # Make the API request
        response = requests.get(url).json()
        doc_abstract = response.get('abstract')
        pdf_url = response.get('pdf_url')
        title = response.get('title')

        # Append the abstract to a text file, skipping the documents with no abstract or PRA related text
        if doc_abstract is not None and 'Paperwork Reduction Act' not in doc_abstract:
            abstracts += doc_abstract + '\n\n'
            formatted_abstracts += f"{title}: \n{doc_abstract} \nFull document: {pdf_url}\n\n"


        # Make a summary that's about 30% the size of the abstracts
        token_count = num_tokens_from_string(abstracts, gpt_model)
        summary_size = round(token_count * 0.3)

        # Check if input text is within the GPT-3 token limit 
        if token_count + summary_size <= 8000 and len(abstracts) > 0:

            # Call the Chat GPT-3.5 API asking for a summary
            response = openai.ChatCompletion.create(
              model=gpt_model,
              messages=[
                {"role": "system", "content": f"You are an expert on US Government who will read abstracts from the Federal Register and generate easy to understand summaries using less than {summary_size} words. Please use proper grammar and punctuation, write in pragraph form, and avoid run-on sentences."},
                {"role": "user", "content": abstracts}
              ],
              max_tokens=summary_size,
              temperature=1,
              presence_penalty=1,
              frequency_penalty=1
            )

            # Extract the summary from the API response
            summary = response["choices"][0]["message"]["content"]

            result += f"{sub_interest}:\n" + summary + "\n\n"

        else:
            result += f"There are no {sub_interest} documents published today.\n\n"

        # Append unsubscribe link
        formatted_abstracts += "\n\n-----------Thank you for using Apprise--------\n\n" + "Have some feedback on your summary? Please reply to this email, it's a monitored inbox.\nWant to unsubscribe from future emails? Click this: https://airtable.com/shrRGWVwlwWDxH9gL"

        # Append abstracts and full text links
        result += "\n\n-----------Federal Register Abstracts--------\n\n" + formatted_abstracts

        # Set up email message
        msg = MIMEMultipart()
        msg['Subject'] = 'Federal Register AI Summary ' + str(date)
        msg['From'] = from_email
        msg['To'] = to_email

        # Add text to email
        body = MIMEText(result)
        msg.attach(body)

        # Send email using SMTP server
        smtp_server = 'smtp.gmail.com'
        smtp_port = 587
        smtp_username = from_email
        smtp_password = GMAIL_PASS

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.sendmail(smtp_username, msg['To'], msg.as_string())
            to_emails.append(to_email)

    print('Sent emails to ', to_emails)

if __name__ == '__main__':
    run()
