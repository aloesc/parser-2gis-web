from __future__ import annotations

import urllib.parse
from typing import Any, Dict, List, Optional


def url_query_encode(query: str) -> str:
    """URL-encode a search query; Russian letters and spaces are left as is (same as the desktop generator)."""
    result = []
    for char in query:
        code = ord(char.lower())
        if 1072 <= code <= 1103 or code in (1105, 32):
            result.append(char)
        else:
            result.append(urllib.parse.quote(char, safe=''))
    return ''.join(result)


def build_search_urls(query: str, cities: List[str], rubric_code: Optional[str],
                      all_cities: Dict[str, Dict[str, Any]]) -> List[str]:
    """Search URLs for `query` in every city, same format as the desktop URLs generator."""
    urls = []
    for code in cities:
        url = f'https://2gis.{all_cities[code]["domain"]}/{code}/search/{url_query_encode(query)}'
        if rubric_code:
            url += f'/rubricId/{rubric_code}'
        urls.append(url + '/filters/sort=name')
    return urls
