from typing import Tuple
from lark import v_args, Tree
from .transformer import TagTransformer
from .inherited import tag_manager, BASE_GRAMMAR
from ...tags.tag_assign import TagAssign as TagAssignStandard

@v_args(inline=True)
class TagAssignTransformer(TagTransformer):
    """The transformer for tag assign"""
    def tag_assign(self, varname, output):
        # type: (str, Tree) -> Tuple[str, Tree]
        """Transform the tag_assign rule"""
        return str(varname), output

@tag_manager.register
class TagAssign(TagAssignStandard):
    BASE_GRAMMAR=BASE_GRAMMAR
    TRANSFORMER=TagAssignTransformer()

    def _render(self, local_vars, global_vars):
        varname, output = self.parsed
        output = output.render(local_vars, global_vars)
        local_vars[varname] = output
        return  ''
