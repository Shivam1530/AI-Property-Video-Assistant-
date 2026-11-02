import unittest

from app.main import Listing, _is_fact_safe
from app.video import build_captions


class CaptionTests(unittest.TestCase):
    def test_captions_only_use_listing_facts(self):
        listing = {"property_name":"Azure Heights","location":"Noida","price":"₹1 Cr","property_type":"2-BHK","features":["Balcony", "Metro nearby"],"cta":"Call today"}
        captions = build_captions(listing)
        text = " ".join(captions)
        for fact in ("Azure Heights", "Noida", "₹1 Cr", "2-BHK", "Balcony", "Metro nearby", "Call today"):
            self.assertIn(fact, text)
        self.assertNotIn("swimming pool", text.lower())

    def test_ai_text_with_unsupported_feature_is_rejected(self):
        listing = Listing(property_name="Azure Heights", location="Noida", price="₹1 Cr", property_type="2-BHK", features=["Balcony"], cta="Call today")
        self.assertFalse(_is_fact_safe("Azure Heights with a swimming pool", listing))


if __name__ == "__main__":
    unittest.main()
