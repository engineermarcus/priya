import unittest

class TestExample(unittest.TestCase):
    def test_addition(self):
        self.assertEqual(1 + 1, 2)
    
    def test_string_concat(self):
        self.assertEqual('hello' + ' world', 'hello world')

if __name__ == '__main__':
    unittest.main()
