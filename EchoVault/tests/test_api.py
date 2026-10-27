"""Smoke tests for the EchoVault local API. Run: python -m unittest discover -s tests."""
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import main


class EchoVaultApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.audio = self.root / "audio"
        self.audio.mkdir()
        self.old_db = main.DB_PATH
        self.old_audio = main.AUDIO_DIR
        main.DB_PATH = self.root / "test.db"
        main.AUDIO_DIR = self.audio
        main.init_db()
        self.client = TestClient(main.app)
        self.summary_patch = patch.object(main, "generate_memory_fields", return_value={
            "summary": "A short, testable summary.",
            "people": ["Riya"],
            "places": ["the garden"],
            "dates": [],
            "themes": ["family stories"],
            "quotes": [],
            "summary_source": "gemma3:4b (test double)",
        })
        self.summary_patch.start()

    def tearDown(self):
        self.summary_patch.stop()
        main.DB_PATH = self.old_db
        main.AUDIO_DIR = self.old_audio
        self.tmp.cleanup()

    def test_health_route(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["storage"], "local")

    def test_create_search_update_and_delete_memory(self):
        created = self.client.post("/api/memories", json={
            "title": "A day by the garden",
            "transcript": "Riya remembers sitting in the garden after the rain.",
        })
        self.assertEqual(created.status_code, 200)
        memory = created.json()
        self.assertEqual(memory["summary"], "A short, testable summary.")

        search = self.client.get("/api/memories", params={"q": "garden"})
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.json()["count"], 1)

        updated = self.client.patch(f"/api/memories/{memory['id']}", json={"title": "Edited story"})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["title"], "Edited story")

        deleted = self.client.delete(f"/api/memories/{memory['id']}")
        self.assertEqual(deleted.status_code, 200)
        missing = self.client.get(f"/api/memories/{memory['id']}")
        self.assertEqual(missing.status_code, 404)


    def test_openrouter_generation_parses_chat_completion(self):
        expected = {
            "summary": "Riya describes planting flowers with her grandmother.",
            "people": ["Riya"],
            "places": ["the garden"],
            "dates": [],
            "themes": ["family memories"],
            "quotes": ["leaving a little colour"],
        }
        response = MagicMock()
        response.json.return_value = {
            "choices": [{"message": {"content": __import__("json").dumps(expected)}}]
        }
        client = MagicMock()
        client.post.return_value = response

        self.summary_patch.stop()
        try:
            with patch.object(main, "AI_PROVIDER", "openrouter"), \
                 patch.object(main, "OPENROUTER_API_KEY", "test-key"), \
                 patch.object(main.httpx, "Client") as http_client:
                http_client.return_value.__enter__.return_value = client
                result = main.generate_memory_fields(
                    "Riya remembers planting flowers with her grandmother in the garden."
                )
        finally:
            self.summary_patch.start()

        self.assertEqual(result["summary"], expected["summary"])
        self.assertEqual(result["people"], ["Riya"])
        self.assertEqual(result["summary_source"], f"openrouter:{main.OPENROUTER_MODEL}")
        request = client.post.call_args
        self.assertIn("/chat/completions", request.args[0])
        self.assertEqual(request.kwargs["headers"]["Authorization"], "Bearer test-key")

    def test_openrouter_connection_test_endpoint(self):
        test_result = {
            "summary": "A short synthetic test summary.",
            "people": ["Mira"],
            "places": ["the courtyard"],
            "dates": [],
            "themes": ["family memories"],
            "quotes": [],
            "summary_source": f"openrouter:{main.OPENROUTER_MODEL}",
        }
        with patch.object(main, "AI_PROVIDER", "openrouter"), \
             patch.object(main, "OPENROUTER_API_KEY", "test-key"), \
             patch.object(main, "_generate_with_openrouter", return_value=test_result):
            response = self.client.post("/api/ai/test")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["provider"], "openrouter")
        self.assertIn("openrouter:", response.json()["summary_source"])

    def test_openrouter_failure_does_not_silently_save_fallback(self):
        self.summary_patch.stop()
        try:
            with patch.object(main, "AI_PROVIDER", "openrouter"), \
                 patch.object(main, "OPENROUTER_API_KEY", ""):
                response = self.client.post("/api/memories", json={
                    "title": "A test story",
                    "transcript": "A transcript long enough to summarize.",
                })
        finally:
            self.summary_patch.start()
        self.assertEqual(response.status_code, 502)
        self.assertIn("OPENROUTER_API_KEY is blank", response.json()["detail"])
        self.assertEqual(self.client.get("/api/memories").json()["count"], 0)

    def test_rejects_short_transcripts(self):
        response = self.client.post("/api/memories", json={"title": "Tiny", "transcript": "abc"})
        self.assertEqual(response.status_code, 422)

    def test_discards_unsaved_audio_upload(self):
        file_id = f"{uuid.uuid4()}.wav"
        path = self.audio / file_id
        path.write_bytes(b"test audio placeholder")
        response = self.client.delete(f"/api/staged-audio/{file_id}")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
