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
from sendgrid.helpers.mail import Mail, Substitution


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

@retry(wait=wait_random_exponential(min=1, max=60), stop=stop_after_attempt(6))
def gpt_summary(gpt_model, summary_size, abstracts):
    summary_limit = 8000 if summary_size > 5333 else int(summary_size * 1.5)
    return openai.ChatCompletion.create(
                  model=gpt_model,
                  messages=[
                    {"role": "system", "content": f"You are an expert on US Government who will read abstracts from the Federal Register and generate easy to understand summaries using less than {summary_size} tokens. Please use proper grammar and punctuation, write in pragraph form, and avoid run-on sentences."},
                    {"role": "user", "content": abstracts}
                  ],
                  max_tokens=summary_limit,
                  temperature=1,
                  presence_penalty=1,
                  frequency_penalty=1
                )


def run():
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
    month = date.strftime("%m")
    day = date.strftime("%d")

    # Define the API endpoint to retrieve today's Federal Register documents
    url = f"https://www.federalregister.gov/api/v1/issues/{year}-{'03'}-{'28'}.json"

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
                    abstracts += doc_abstract + '<br><br>'
                    formatted_abstracts += f"{title}: <br>{doc_abstract} <br>Full document: {pdf_url}<br><br>"

            # Make a summary that's about 30% the size of the abstracts
            token_count = num_tokens_from_string(abstracts, gpt_model)
            summary_size = round(token_count * 0.3)

            # Check if input text is within the GPT-3 token limit 
            if token_count + summary_size <= 8000 and len(abstracts) > 0:

                # Call the Chat GPT-3.5 API asking for a summary
                response = gpt_summary(gpt_model, summary_size, abstracts)

                # Extract the summary from the API response
                summary = response["choices"][0]["message"]["content"]

                result += f"{sub_interest}:<br>" + summary + "<br><br>"

            else:
                result += f"There are no {sub_interest} documents published today.<br><br>"

        # update to your dynamic template id from the UI
        TEMPLATE_ID = 'd-9fd5ba827e824a5ea38c2d5607883370'
        subject_line = 'Apprise Daily Summary ' + str(date)
        
        message = Mail(
            from_email=from_email,
            to_emails=to_email,
            subject=subject_line
        )

        # set the dynamic template content of the Mail object
        message.template_id = TEMPLATE_ID
        message.dynamic_template_data = {
            'summary': [Substitution('1', result)],
            'full_text': [subject_line]
        }

        print(result)
        print(formatted_abstracts)

        try:
            sg = SendGridAPIClient(os.environ.get('SENDGRID_API_KEY'))
            response = sg.send(message)
            code, body, headers = response.status_code, response.body, response.headers
            print(f"Response code: {code}")
            print(f"Response headers: {headers}")
            print(f"Response body: {body}")
            print("Dynamic Messages Sent!")
        except Exception as e:
            print("Error: {0}".format(e))

        to_emails.append(to_email)

    print('Sent emails to ', to_emails)

if __name__ == '__main__':
    run()
