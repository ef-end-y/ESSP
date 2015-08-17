from setuptools import setup, find_packages


setup(
    name='essp-api',
    version='1.0.0',
    description='ESSP Library',
    long_description='Encrypted Smiley Secure Protocol Python Library',
    url='https://github.com/ef-end-y/ESSP',
    author='Stanislav Volik',
    author_email='max.begemot -at- gmail.com',
    license='thinking..',
    classifiers=[
        'Programming Language :: Python :: 2',
        'Topic :: Software Development :: Libraries :: Python Modules',
    ],
    keywords='essp banknote validators',
    packages=find_packages(exclude=['contrib', 'docs', 'tests*']),
    install_requires=['pyserial'],
    tests_require=['nose'],
)
