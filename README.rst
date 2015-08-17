Encrypted Smiley Secure Protocol Python Library
===============================================

Installation
------------

.. code-block:: bash

  git clone https://github.com/ef-end-y/ESSP.git
  cd ESSP/
  pip install .

Examples
--------

.. code-block:: python

  import time
  from essp_api import EsspApi
  essp = EsspApi('/dev/ttyACM0')
  essp.enable()
  while True:
      for p in essp.poll():
          if p['status'] == essp.CREDIT_NOTE:
              print 'A note (code=%s) has passed through the device' % p['param']
      time.sleep(0.5)
