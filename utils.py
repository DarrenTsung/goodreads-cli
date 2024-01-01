import re

def stripped_title(title):
    """
    Remove common words and non-word characters from a title and lowercase it.
    """
    common_words = {
        'and', 'the', 'of', 'in', 'a', 'to', 'for', 'with', 'on', 'at',
        'from', 'by', 'about', 'as', 'into', 'like', 'through', 'after',
        'over', 'between', 'out', 'against', 'during', 'without', 'before',
        'under', 'around', 'among'
    }
    # Remove non-word characters and split into words
    words = re.sub(r'[^\w\s]', '', title).lower().split()
    # Filter out common words
    filtered_words = [word for word in words if word not in common_words]
    # Join the words back into a string
    stripped = ' '.join(filtered_words)
    return stripped

def stripped(text):
    text = re.sub(r'[^\w\s]', '', text).lower().strip()
    return text 