import sys
import logging
import time
from ESSP.api import ESSP

k = ESSP('/dev/ttyACM0', logger_handler=logging.StreamHandler(sys.stdout))
k.sync()
k.enable_higher_protocol()
k.set_inhibits(k.easy_inhibit([1, 1, 1, 1, 1, 1, 1]), '0')
k.enable()
while True:
    poll = k.poll()
    for p in poll:
        print p
        if p['status'] == ESSP.READ_NOTE and p['param'] > 0:
            for i in range(0, 10):
                k.hold()
                print 'Hold...'
                time.sleep(0.5)
            if p['param'] == 2:
                k.reject_note()
        if p['status'] == ESSP.CREDIT_NOTE:
            print 'Credit: ' + str(p['param'])
    time.sleep(0.5)

