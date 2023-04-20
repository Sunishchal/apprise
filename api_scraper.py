import base64
import smtplib
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
import tiktoken
import openai
import os
import requests
import datetime
from pyairtable import Table
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_random_exponential
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail


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

def run(i):
    # Retrieve API Keys & Gmail app password from env variables
    OPENAI_KEY = os.getenv('OPENAI_KEY')
    openai.api_key = OPENAI_KEY
    gpt_model = 'gpt-4'

    AIRTABLE_API_KEY = os.getenv('AIRTABLE_API_KEY')
    AIRTABLE_BASE_ID = 'appaIzcAYJHAUCqBM'
    subscriber = Table(AIRTABLE_API_KEY, AIRTABLE_BASE_ID, 'Subscriber Dev')
    # subscriber = Table(AIRTABLE_API_KEY, AIRTABLE_BASE_ID, 'Subscriber')
    interest = Table(AIRTABLE_API_KEY, AIRTABLE_BASE_ID, 'Interest')

    GMAIL_PASS = os.getenv('GMAIL_PASS')
    from_email = 'apprise.summaries@gmail.com'
    to_emails = []

    # Create variables for year, month, and date in ISO format
    # The register is published daily at 6am EST, so default to yesterday if it's too early in the day
    if datetime.datetime.now().hour < 11: # 11am UTC is 6am EST
        date = datetime.date.today() - datetime.timedelta(1)
    else:
        date = datetime.date.today()
        
    year = date.strftime("%Y")
    month = '03' # date.strftime("%m")
    day = str(int(date.strftime("%d")) + i)
    print(f'{year}-{month}-{day}')

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
        result = ""
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
                    formatted_abstracts += f"{title}: \n{doc_abstract} \n"#Full document: {pdf_url}\n\n"

    # write formatted_abstracts to a text file
    with open('abstracts.txt', 'a') as f:
        f.write(formatted_abstracts)

if __name__ == '__main__':
    for i in range(31):
        run(i)
