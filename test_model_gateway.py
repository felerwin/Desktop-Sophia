import unittest

from ember import RetrySchedule, response_content


class ModelGatewayTests(unittest.TestCase):
    def test_request_image_is_optional(self):
        text_only = response_content("hello")
        with_image = response_content("look", "data:image/jpeg;base64,abc")
        self.assertEqual(len(text_only[0]["content"]), 1)
        self.assertEqual(with_image[0]["content"][1]["type"], "input_image")

    def test_retry_schedule_is_bounded_exponential(self):
        retry = RetrySchedule(2, 2)
        self.assertEqual(retry.delay_after(0), 2)
        self.assertEqual(retry.delay_after(1), 4)
        self.assertIsNone(retry.delay_after(2))


if __name__ == "__main__":
    unittest.main()
