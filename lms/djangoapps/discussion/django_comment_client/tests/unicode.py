# pylint: disable=missing-module-docstring


class UnicodeTestMixin:  # pylint: disable=missing-class-docstring
    def test_ascii(self):
        self._test_unicode_data("This post contains ASCII.")

    def test_latin_1(self):
        self._test_unicode_data("Thís pøst çòñtáins Lätin-1 tæxt")

    def test_CJK(self):
        self._test_unicode_data("ｲんﾉ丂 ｱo丂ｲ co刀ｲﾑﾉ刀丂 cﾌズ")

    def test_non_BMP(self):
        self._test_unicode_data("𝕋𝕙𝕚𝕤 𝕡𝕠𝕤𝕥 𝕔𝕠𝕟𝕥𝕒𝕚𝕟𝕤 𝕔𝕙𝕒𝕣𝕒𝕔𝕥𝕖𝕣𝕤 𝕠𝕦𝕥𝕤𝕚𝕕𝕖 𝕥𝕙𝕖 𝔹𝕄ℙ")

    def test_special_chars(self):
        self._test_unicode_data(
            "\" This , post > contains < delimiter ] and [ other } special { characters ; that & may ' break things"
        )

    def test_string_interp(self):
        self._test_unicode_data("This post contains %s string interpolation #{syntax}")
