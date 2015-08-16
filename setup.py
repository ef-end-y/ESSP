from setuptools import setup, find_packages
from codecs import open
from os import path

here = path.abspath(path.dirname(__file__))

with open(path.join(here, 'DESCRIPTION.rst'), encoding='utf-8') as f:
    long_description = f.read()

setup(
    name='ESSP',
    version='1.0.0',
    description='ESSP Library',
    long_description='Encrypted Smiley Secure Protocol Python Library',
    url='https://github.com/ef-end-y/ESSP',
    author='Stanislav Volik',
    author_email='max.begemot@gmail.com',
    license='thinking..',
    classifiers=[
        'Programming Language :: Python :: 2',
        'Topic :: Software Development :: Libraries :: Python Modules',
    ],
    keywords='essp banknote validators',
    packages=find_packages(exclude=['contrib', 'docs', 'tests*']),
    install_requires=['pyserial'],
)
