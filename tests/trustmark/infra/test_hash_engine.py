# Run tests with: pytest tests/trustmark/infra/test_hash_engine.py
import pytest
from trustmark.infra.hash_engine import generate_hash, generate_hash_from_bytes


class TestHashEngine:
    """Tests for the hash engine functions."""

    def test_generate_hash_canonicalization(self):
        """Test that different line endings result in the same hash."""
        content_rn = "line1\r\nline2"
        content_n = "line1\nline2"

        assert generate_hash(content_rn) == generate_hash(content_n)

    def test_hash_with_file_content(self, tmp_path):
        """Test hashing content that mirrors a file's data."""
        d = tmp_path / "sub"
        d.mkdir()
        p = d / "hello.txt"
        test_content = "Hello World"
        p.write_text(test_content)

        file_content = p.read_text()
        result_hash = generate_hash(file_content)

        # Verify against a known SHA-256 hash for "Hello World"
        expected_hash = "a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e"
        assert result_hash == expected_hash

    def test_generate_hash_from_bytes(self):
        """Test raw byte hashing."""
        data = b"test data"
        expected = "916f0027a575074ce72a331777c3478d6513f786a591bd892da1a577bf2335f9"
        assert generate_hash_from_bytes(data) == expected

    def test_generate_hash_empty_string(self):
        """Test that hashing an empty string produces the correct hash."""
        expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert generate_hash("") == expected

    def test_generate_hash_from_bytes_empty(self):
        """Test that hashing empty bytes produces the correct hash."""
        expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert generate_hash_from_bytes(b"") == expected

    def test_generate_hash_invalid_input_raises(self):
        """Test that invalid input types raise exceptions."""
        with pytest.raises(AttributeError):
            generate_hash(None)

    def test_generate_hash_from_bytes_invalid_input_raises(self):
        """ "Test that invalid input types raise exceptions for byte hashing."""
        with pytest.raises(TypeError):
            generate_hash_from_bytes("not bytes")
