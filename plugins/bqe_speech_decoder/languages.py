"""Languages supported by the default multilingual Whisper model (no inference dependency)."""
LANGUAGES = dict(item.split(':', 1) for item in (
    'en:English|es:Spanish|pt:Portuguese|af:Afrikaans|am:Amharic|ar:Arabic|as:Assamese|az:Azerbaijani|'
    'ba:Bashkir|be:Belarusian|bg:Bulgarian|bn:Bengali|bo:Tibetan|br:Breton|bs:Bosnian|ca:Catalan|'
    'cs:Czech|cy:Welsh|da:Danish|de:German|el:Greek|et:Estonian|eu:Basque|fa:Persian|fi:Finnish|'
    'fo:Faroese|fr:French|gl:Galician|gu:Gujarati|ha:Hausa|haw:Hawaiian|he:Hebrew|hi:Hindi|'
    'hr:Croatian|ht:Haitian Creole|hu:Hungarian|hy:Armenian|id:Indonesian|is:Icelandic|it:Italian|'
    'ja:Japanese|jw:Javanese|ka:Georgian|kk:Kazakh|km:Khmer|kn:Kannada|ko:Korean|la:Latin|'
    'lb:Luxembourgish|ln:Lingala|lo:Lao|lt:Lithuanian|lv:Latvian|mg:Malagasy|mi:Maori|mk:Macedonian|'
    'ml:Malayalam|mn:Mongolian|mr:Marathi|ms:Malay|mt:Maltese|my:Myanmar|ne:Nepali|nl:Dutch|'
    'nn:Norwegian Nynorsk|no:Norwegian|oc:Occitan|pa:Punjabi|pl:Polish|ps:Pashto|ro:Romanian|'
    'ru:Russian|sa:Sanskrit|sd:Sindhi|si:Sinhala|sk:Slovak|sl:Slovenian|sn:Shona|so:Somali|'
    'sq:Albanian|sr:Serbian|su:Sundanese|sv:Swedish|sw:Swahili|ta:Tamil|te:Telugu|tg:Tajik|'
    'th:Thai|tk:Turkmen|tl:Tagalog|tr:Turkish|tt:Tatar|uk:Ukrainian|ur:Urdu|uz:Uzbek|'
    'vi:Vietnamese|yi:Yiddish|yo:Yoruba|zh:Mandarin Chinese'
).split('|'))


REGIONAL_LANGUAGES = {'pt-BR': 'Portuguese (Brazil)', 'pt-PT': 'Portuguese (Portugal)'}

def validate_language(language):
    if not isinstance(language, str) or language not in (*LANGUAGES, *REGIONAL_LANGUAGES, 'auto'):
        raise ValueError('Choose a supported speech language or auto')
    return language


def language_name(language):
    return {**LANGUAGES, **REGIONAL_LANGUAGES}.get(language, 'Undetermined' if language == 'und' else language)
