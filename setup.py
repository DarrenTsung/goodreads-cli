from setuptools import setup, find_packages

setup(
    name='goodreads-cli',
    version='0.1.0',
    packages=find_packages(),
    install_requires=[
        'requests',
        'beautifulsoup4',
        'prettytable',
        'fuzzywuzzy',
        'python-Levenshtein',  # Add python-Levenshtein to remove fuzzywuzzy warning
    ],
    entry_points={
        'console_scripts': [
            'goodreads-cli=main:main',
        ],
    },
    author='Darren Tsung',
    author_email='darren.tsung@gmail.com',
    description='A CLI tool for interacting with Reddit posts.',
    license='MIT',
    keywords='reddit cli',
    url='https://github.com/darrentsung/goodreads-cli',
)
