from urllib.parse import urlsplit

from translator import TranslatorLLM


def gateway_get_stub(gateway_get, other_get):
    gateway = urlsplit(TranslatorLLM(api_token='test-token').api_url)

    def get(url, **kwargs):
        parsed = urlsplit(url)
        if (parsed.scheme, parsed.netloc) == (gateway.scheme, gateway.netloc):
            if parsed.path not in ('/api/status', '/api/usage/token/'):
                raise AssertionError('Unexpected gateway GET')
            return gateway_get(url, **kwargs)
        return other_get(url, **kwargs)

    return get
