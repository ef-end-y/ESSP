Encrypted Smiley Secure Protocol Python Library
===============================================

Examples
--------

.. code-block:: python

  from ESSP.api import ESSP
  essp = ESSP('/dev/ttyACM0')
  essp.enable()
  while True:
      for p in essp.poll():
          if p['status'] == ESSP.CREDIT_NOTE:
              print 'A note has passed through the device. A note code: ' + str(p['param'])
      time.sleep(0.5)
