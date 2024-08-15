import re

def stripped_title(title):
    # Remove non-word characters and split into words
    words = re.sub(r'[^\w\s]', '', title).lower().split()
    # Join the words back into a string
    stripped = ' '.join(words)
    return stripped

def stripped(text):
    text = re.sub(r'[^\w\s]', '', text).lower().strip()
    return text 