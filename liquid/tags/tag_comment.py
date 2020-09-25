"""Tag comment

```liquid
{% comment %}
...
{% endcomment %}
```
"""

from .manager import tag_manager
from .tag import Tag

@tag_manager.register
class TagComment(Tag):
    """The comment tag"""
    RAW = True
    def _render(self, local_vars, global_vars):
        return ''
