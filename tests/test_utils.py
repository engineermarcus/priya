import unittest
import os
import tempfile


class TestMathOperations(unittest.TestCase):
    def test_subtraction(self):
        self.assertEqual(5 - 3, 2)

    def test_multiplication(self):
        self.assertEqual(4 * 5, 20)

    def test_division(self):
        self.assertEqual(10 / 2, 5)

    def test_floor_division(self):
        self.assertEqual(10 // 3, 3)

    def test_modulo(self):
        self.assertEqual(10 % 3, 1)

    def test_exponentiation(self):
        self.assertEqual(2 ** 3, 8)


class TestStringOperations(unittest.TestCase):
    def test_uppercase(self):
        self.assertEqual("hello".upper(), "HELLO")

    def test_lowercase(self):
        self.assertEqual("HELLO".lower(), "hello")

    def test_strip(self):
        self.assertEqual("  hello  ".strip(), "hello")

    def test_split(self):
        self.assertEqual("a,b,c".split(","), ["a", "b", "c"])

    def test_contains(self):
        self.assertIn("world", "hello world")


class TestListOperations(unittest.TestCase):
    def test_append(self):
        lst = [1, 2, 3]
        lst.append(4)
        self.assertEqual(lst, [1, 2, 3, 4])

    def test_extend(self):
        lst1 = [1, 2]
        lst2 = [3, 4]
        lst1.extend(lst2)
        self.assertEqual(lst1, [1, 2, 3, 4])

    def test_remove(self):
        lst = [1, 2, 3, 2]
        lst.remove(2)
        self.assertEqual(lst, [1, 3, 2])

    def test_pop(self):
        lst = [1, 2, 3]
        popped = lst.pop()
        self.assertEqual(popped, 3)
        self.assertEqual(lst, [1, 2])

    def test_index(self):
        lst = ["a", "b", "c"]
        self.assertEqual(lst.index("b"), 1)

    def test_slicing(self):
        lst = [1, 2, 3, 4, 5]
        self.assertEqual(lst[1:4], [2, 3, 4])


class TestDictOperations(unittest.TestCase):
    def test_add_key(self):
        d = {"a": 1}
        d["b"] = 2
        self.assertEqual(d, {"a": 1, "b": 2})

    def test_get(self):
        d = {"a": 1, "b": 2}
        self.assertEqual(d.get("a"), 1)
        self.assertIsNone(d.get("c"))

    def test_keys(self):
        d = {"a": 1, "b": 2}
        self.assertEqual(set(d.keys()), {"a", "b"})

    def test_values(self):
        d = {"a": 1, "b": 2}
        self.assertEqual(set(d.values()), {1, 2})

    def test_pop(self):
        d = {"a": 1, "b": 2}
        val = d.pop("a")
        self.assertEqual(val, 1)
        self.assertEqual(d, {"b": 2})


class TestFileOperations(unittest.TestCase):
    def test_file_create_and_read(self):
        with tempfile.NamedTemporaryFile(mode='w+', delete=False) as f:
            f.write("test content")
            f.flush()
            temp_path = f.name
        
        try:
            with open(temp_path, 'r') as f:
                content = f.read()
            self.assertEqual(content, "test content")
        finally:
            os.unlink(temp_path)

    def test_file_exists(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            temp_path = f.name
        
        try:
            self.assertTrue(os.path.exists(temp_path))
        finally:
            os.unlink(temp_path)


class TestEdgeCases(unittest.TestCase):
    def test_empty_list(self):
        self.assertEqual(len([]), 0)

    def test_empty_string(self):
        self.assertEqual(len(""), 0)

    def test_empty_dict(self):
        self.assertEqual(len({}), 0)

    def test_division_by_zero(self):
        with self.assertRaises(ZeroDivisionError):
            1 / 0

    def test_key_error(self):
        d = {"a": 1}
        with self.assertRaises(KeyError):
            _ = d["b"]

    def test_index_error(self):
        lst = [1, 2, 3]
        with self.assertRaises(IndexError):
            _ = lst[10]


if __name__ == '__main__':
    unittest.main()
