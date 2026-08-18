import unittest

from spotify_control import SpotifyClient


class FakeSpotify(SpotifyClient):
    def __init__(self):
        self.actions = []

    def currently_playing(self):
        return {
            "item": {
                "name": "Night Drive",
                "artists": [{"name": "Test Artist"}],
            }
        }

    def pause(self):
        self.actions.append("pause")

    def resume(self):
        self.actions.append("resume")

    def next_track(self):
        self.actions.append("next")

    def previous_track(self):
        self.actions.append("previous")


class SpotifyVoiceCommandTests(unittest.TestCase):
    def setUp(self):
        self.spotify = FakeSpotify()

    def assert_command(self, phrase, action):
        handled, reply = self.spotify.handle_voice_command(phrase)
        self.assertTrue(handled)
        self.assertTrue(reply)
        self.assertEqual(self.spotify.actions[-1], action)

    def test_now_playing(self):
        handled, reply = self.spotify.handle_voice_command("What’s playing?")
        self.assertTrue(handled)
        self.assertEqual(reply, "This is Night Drive by Test Artist.")

    def test_controls(self):
        self.assert_command("Pause the music.", "pause")
        self.assert_command("Resume Spotify.", "resume")
        self.assert_command("Skip this song.", "next")
        self.assert_command("Go back one song.", "previous")

    def test_ordinary_conversation_is_not_a_command(self):
        handled, reply = self.spotify.handle_voice_command("I paused before answering.")
        self.assertFalse(handled)
        self.assertIsNone(reply)


if __name__ == "__main__":
    unittest.main()
