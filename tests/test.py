from essp_api import EsspApi
import unittest
import serial


class SerialMock(object):
    POLL_CMD = ('7f0001071188'.decode('hex'), '7f8001071202'.decode('hex'))

    def __init__(self, *args, **kwargs):
        self.response = ''
        self.poll_count = 0
        self.sent = ''

    def write(self, data):
        self.sent = data
        if data in self.POLL_CMD:
            self.poll_count += 1
            step = self.poll_count % 10
            if step == 2:
                # a note is in the process of being scanned
                self.response = '7f8003f0ef00cfca'
                return
            if step == 3:
                # valid note has been scanned, 4 channel
                self.response = '7f8003f0ef04d44a'
                return
            if step == 4:
                # a note has passed through the device
                self.response = '7f0004f0ee04cce0d6'
                return
        self.response = '7f8001f02380'

    def read(self, count=None):
        res = self.response[0:count*2]
        self.response = self.response[count*2:]
        return res.decode('hex')

    def inWaiting(self):
        return len(self.response)


serial.Serial = SerialMock


class TestESSP(unittest.TestCase):
    def test(self):
        p = EsspApi('')
        self.assertTrue(p.sync())
        self.assertIsInstance(p.poll(), list)
        self.assertIn(''.join(['%02x' % ord(c) for c in p._serial.sent]), ('7f8001071202', '7f0001071188'))
        res = p.poll()[0]
        self.assertEqual(res['status'], p.READ_NOTE)
        self.assertEqual(res['param'], 0)
        res = p.poll()[0]
        self.assertEqual(res['status'], p.READ_NOTE)
        self.assertEqual(res['param'], 4)
        res = p.poll()[0]
        self.assertEqual(res['status'], p.CREDIT_NOTE)
        self.assertEqual(res['param'], 4)
        self.assertEqual(p.easy_inhibit([1, 0, 1, 0, 1, 1, 1, 1]), 'f5')


if __name__ == '__main__':
    unittest.main()

