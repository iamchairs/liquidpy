"""The top-level parser for liquid template"""
import re
from functools import partial
from collections import deque, OrderedDict
from varname import namedtuple
from diot import Diot
from lark import v_args, Lark, Transformer as LarkTransformer
from lark.exceptions import VisitError as LarkVisitError
# load all shared tags
from . import tags # pylint: disable=unused-import
from ..tagmgr import get_tag
from ..config import LIQUID_LOG_INDENT
from ..exceptions import (
    TagUnclosed, EndTagUnexpected,
    TagWrongPosition
)

TagContext = namedtuple(['template_name', # pylint: disable=invalid-name
                         'context_getter',
                         'line',
                         'column',
                         'logger'])

@v_args(inline=True)
class Transformer(LarkTransformer):
    """Transformer class to transform the trees/tokens

    Attributes:
        _stacks (deque): The stack used to handle the relationships between
            tags
        _direct_tags (list): The direct tags of the ROOT tag
    """

    def __init__(self, config, template_info):
        """Construct"""
        super().__init__()
        self.config = config
        self._stack = deque()
        self._direct_tags = []
        self._template_info = template_info

    def output_tag(self, tagstr):
        """The output tag: {{ ... }}, or content of {% echo ... %}"""
        # Should we allow: {{--1}}?
        # This will be currently rendered as 1, but -1 is intended.
        tagdata = tagstr[2:-2].strip('-').strip()
        tag = get_tag('__OUTPUT__', tagdata, self._tag_context(tagstr))
        self._opening_tag(tag)
        return tag

    def open_tag(self, tagstr):
        """Open a tag"""
        tagname, tagdata = self._clean_tagstr(tagstr)
        tag = get_tag(tagname, tagdata, self._tag_context(tagstr))
        self._opening_tag(tag)
        return tag

    def close_tag(self, tagstr):
        """Handle tag relationships when closing a tag."""
        if not self._stack:
            raise EndTagUnexpected(tagstr)

        tagname, _ = self._clean_tagstr(tagstr)
        tagname = tagname[3:]

        last_tag = self._stack.pop()
        if last_tag.name == tagname:
            # collapse VOID to False for maybe VOID tags
            if last_tag.VOID == 'maybe':
                self.config.logger.debug(
                    '    Collapsing tag (VOID: maybe -> False): %s', last_tag
                )
                last_tag.VOID = False
            self.config.logger.info('%s<EndTag(name=%s)>',
                                    LIQUID_LOG_INDENT * last_tag.level,
                                    last_tag.name)
        elif last_tag.parent and last_tag.parent.name == tagname:
            assert last_tag.parent is self._stack[-1]
            self._stack.pop()
            self.config.logger.info('%s<EndTag(name=%s)>',
                                    LIQUID_LOG_INDENT * last_tag.parent.level,
                                    last_tag.parent.name)
            # we have to check if children of last_tag's parent have been closed
            # in other words, last_tag's siblings
            most_prior = last_tag._most_prior()
            # of course it is not VOID
            # If a tag needs parent, supposingly, the parent will close
            # for it
            if (most_prior and (
                    not most_prior.PARENT_TAGS or
                    '' in most_prior.PARENT_TAGS
                )):
                raise TagUnclosed(
                    most_prior._format_error(most_prior)
                )
        else:
            # now we tried to
            # 1) close the direct tag (last_tag) or
            # 2) the parent of the last_tag
            # However, for 2) we need to check if the tags inside the parent
            # tag have been closed
            # In the case of
            #
            # {% for ... %}
            #     {% if ... %}
            # {% endfor %}
            #
            # "endfor" will close "for ...", but "if ..."
            # remains unclosed
            #
            # if last_tag has siblings, we check the most prior one
            most_prior = last_tag._most_prior()
            if most_prior:
                if most_prior.name == tagname:
                    # nothing to do, since it's not in the stack already
                    self.config.logger.info(
                        '%s<EndTag(name=%s)>',
                        LIQUID_LOG_INDENT * most_prior.level,
                        tagname
                    )
                # if it has to be closed
                elif (not most_prior.PARENT_TAGS or
                      '' in most_prior.PARENT_TAGS):
                    raise TagUnclosed(
                        most_prior._format_error(most_prior)
                    )
            # if last_tag doesn't have first sibling
            # check itself
            elif (not last_tag.PARENT_TAGS or
                  '' in last_tag.PARENT_TAGS):
                raise TagUnclosed(
                    last_tag._format_error(last_tag)
                )
            else:
                raise EndTagUnexpected(
                    self._format_error(tagstr, EndTagUnexpected)
                )

    def raw_tag(self, content):
        """The raw tag, where the tags are not interpreted by liquidpy"""
        cleaned = re.sub(r'^\{%-?\s*raw\s*-?%\}|\{%-?\s*endraw\s*-?%\}$',
                         '', str(content))
        tag = get_tag('__RAW__', cleaned, self._tag_context(content))
        self._opening_tag(tag)
        return tag

    def literal_tag(self, tree):
        """The literal_tag from master grammar

        Args:
            tree (lark.Tree): The tree identified by the rule

        Returns:
            TagLiteral: The literal tag
        """
        tag = get_tag('__LITERAL__', tree.value, self._tag_context(tree))
        self._opening_tag(tag)
        return tag

    def literal_tag_both_compact(self, tagstr):
        """Literal with both end compact"""
        tagstr.value = tagstr.strip()
        return tagstr

    def literal_tag_left_compact(self, tagstr):
        """Literal with left end compact"""
        tagstr.value = tagstr.lstrip()
        return tagstr

    def literal_tag_right_compact(self, tagstr):
        """Literal with right end compact"""
        tagstr.value = tagstr.rstrip()
        return tagstr

    def literal_tag_non_compact(self, tagstr):
        """Literal with no compact"""
        return tagstr

    literal_tag_first = literal_tag_non_compact
    literal_tag_first_right_compact = literal_tag_right_compact

    def _tag_context(self, token):
        """Get the TagContext object to attached to each Tag for
        exceptions"""
        if self._template_info:
            return TagContext(
                *self._template_info, token.line, token.column,
                self.config.logger
            )

        # return TagContext('<unknown>',
        #                   lambda line, context_lines: {},
        #                   token.line,
        #                   token.column,
        #                   self.config.logger)


    def _format_error(self, tag, error=None):
        if isinstance(error, Exception):
            error = f"[{error.__class__.__name__}: {error}] {tag}\n"
        elif callable(error):
            error = f"[{error.__name__}] {tag}\n"
        elif error:
            error = f"{error}: {tag}\n"
        else:
            error = ''


        context = self._tag_context(tag)
        formatted = [
            error,
            f'{context.template_name}:'
            f'{context.line}:{context.column}',
            '-' * 80
        ]
        context_lines = context.context_getter(line=context.line)
        lineno_width = len(str(max(context_lines)))
        for lineno, line in context_lines.items():
            indicator = ('>' if context.line == lineno
                         else ' ')
            formatted.append(f'{indicator} {str(lineno).ljust(lineno_width)}'
                             f'. {line}')

        return '\n'.join(formatted) + '\n'

    def _opening_tag(self, tag):
        """Handle the relationships between tags when a tag is opening

        When the stack is empty, we treat the tag as direct tag (to ROOT),
        Then these tags will be rendered directly by ROOT tag (a virtual tag
        that deals with all direct child tags)

        If it is not empty, then that means this tag is a child of
        the last tag (parent) of the stack, we attach it to the children of the
        parent, and attach the parent to the parent of the child as well
        (useful to detect when a tag is inside the one that it is supposed to
        be. For exaple, `cycle` should be with `for` tag. Then we are able to
        trace back the parent to see if `for` is one of its parents)

        Also if VOID is False, meaning that this tag can have children, we
        need to push it into the stack.

        Another case we can do for the extended mode is that, we can allow
        tags to be both VOID and non-VOID.

        We can also do VOID = 'maybe' case. However, this type of tags can only
        have literals in it. When we hit the end tag of it, then we know it is
        a VOID = False tag. But before that, if we hit the other open tags,
        close tag of its parent or EOF then we know if it is a VOID = True tag,
        we need to move all the children of it to the upper level (its parent)

        For cases of set of tags appearing together, non-first tags should have
        PRIOR_TAGS and PARENT_TAGS defined, we need them to validate if the tag
        is in the right place or within the right parent. More than that,
        we also need the PRIOR_TAGS to prevent this tag to be treated as a
        child of its prior tags
        """
        if not self._stack:
            if tag.PARENT_TAGS and '' not in tag.PARENT_TAGS:
                raise TagWrongPosition(
                    f"Expecting parents {tag.PARENT_TAGS}: {tag}"
                )
            if tag.PRIOR_TAGS and '' not in tag.PRIOR_TAGS:
                raise TagWrongPosition(f'{tag} requires a prior tag.')

            tag.level = 0
            self.config.logger.info(
                '%s%s',
                LIQUID_LOG_INDENT * tag.level, tag
            )
            self._direct_tags.append(tag)
        else:
            # assign siblings
            if tag.PRIOR_TAGS and self._stack[-1].name in tag.PRIOR_TAGS:
                prev_tag = self._stack.pop()
                prev_tag.next = tag
                tag.prev = prev_tag

                tag.level = prev_tag.level

            # prior tags should not have VOID maybe, check?
            elif self._stack[-1].VOID == 'maybe':
                void_tag = self._stack.pop()
                void_tag.VOID = True
                if not void_tag.parent:
                    self._direct_tags.extend(void_tag.children)
                else:
                    void_tag.parent.children.extend(void_tag.children)

                del void_tag.children[:]

            if self._stack:
                self._stack[-1].children.append(tag)
                tag.parent = self._stack[-1]
                tag.level = tag.parent.level + 1
                self.config.logger.info('%s%s',
                                        LIQUID_LOG_INDENT * tag.level,
                                        tag)

            # parent check
            # we need to do it after siblings assignment, because
            # direct parent could also valid from first sibling's
            # direct parent
            if not tag._parent_check():
                raise TagWrongPosition(
                    f"Expecting parents {tag.PARENT_TAGS}: {tag}"
                )

        if not tag.VOID or tag.VOID == 'maybe':
            self._stack.append(tag)

    def _clean_tagstr(self, tagdata):
        """Clean up the tag data, removing the tag signs

        Args:
            tagdata (lark.Tree): The whole content of the tag,
                including the tag signs
        Returns:
            tuple: the tagname and tagdata without the tag signs
        """
        tagdata = tagdata[2:-2].strip('-').strip()
        parts = tagdata.split(maxsplit=1)
        return parts.pop(0), parts[0] if parts else ''

    def start(self, *args): # pylint: disable=unused-argument
        """Turn the start rule to a TagRoot object"""
        if self._stack:
            last_tag = self._stack.pop()
            if last_tag.VOID != 'maybe':
                raise TagUnclosed(last_tag._format_error(last_tag))
            # this tag is VOID, take children out
            last_tag.VOID = True
            self._direct_tags.extend(last_tag.children)
            del last_tag.children[:]
        # if we still have unclosed tags
        if self._stack:
            raise TagUnclosed(self._stack.pop())

        self.config.logger.info('')

        root = get_tag('__ROOT__', None, Diot(line=1, column=1,
                                              logger=self.config.logger))
        root.children = self._direct_tags
        return root

class Parser:
    """The parser object to parse the whole template

    Attributes:
        GRAMMAR (str): The lark grammar for the whole template
        TRANSFORMER (lark.Transformer): The transformer to
            transform the trees/tokens
    """
    GRAMMAR = None
    TRANSFORMER = Transformer

    def __init__(self, config):
        self.config = config

    def parse(self, template_string, template_name):
        """Parse the template string

        Args:
            template_string (str): The template string
            template_name (str): The template name, used in exceptions
        Returns:
            TagRoot: The TagRoot object, allowing later rendering
                the template with envs/context
        """
        parser = Lark(self.GRAMMAR, parser='lalr')

        self.config.logger.info('PARSING %s', template_name)
        self.config.logger.info('-' * min(40, len(template_name) + 8))

        tree = parser.parse(template_string)

        try:
            return self.TRANSFORMER(
                self.config,
                (template_name,
                 partial(self._context_getter, template_string=template_string))
            ).transform(tree)
        except LarkVisitError as verr:
            raise verr.orig_exc from None

    def _context_getter(
            self,
            template_string,
            line,
            context_lines=10
    ):
        """Get the context lines form the template for expcetions"""
        # [1,2,...9]
        template_lines = template_string.splitlines()
        # line = 8, pre_/post_lines = 5
        # should show: 3,4,5,6,7, 8, 9
        pre_lines = post_lines = context_lines // 2
        pre_lineno = max(1, line - pre_lines) # 3
        post_lineno = min(len(template_lines), line + post_lines) # 9
        return OrderedDict(zip(
            range(pre_lineno, post_lineno+1),
            template_lines[(pre_lineno-1):post_lineno]
        ))
