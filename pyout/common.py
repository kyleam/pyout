"""Common components for styled output.

This modules contains things that would be shared across outputters if there
were any besides Tabular.  The Tabular class, though, still contains a good
amount of general logic that should be extracted if any other outputter is
actually added.
"""

from collections import defaultdict, Mapping, Sequence
from functools import partial
import inspect

from pyout import elements
from pyout.field import Field, Nothing

NOTHING = Nothing()


class RowNormalizer(object):
    """Transform various input data forms to a common form.

    An un-normalized can be one of three kinds:

      * a mapping from column names to keys

      * a sequence of values in the same order as `columns`

      * any other value will be taken as an object where the column values can
        be accessed via an attribute with the same name

    To normalized a row, it is

      * converted to a dict that maps from column names to values

      * all callables are stripped out and replaced with their initial values

      * if the value for a column is missing, it is replaced with a Nothing
        instance whose value is specified by the column's style (an empty
        string by default)

    Parameters
    ----------
    columns : sequence of str
        Column names.
    style : dict, optional
        Column styles.

    Attributes
    ----------
    methods : callable
        A function that takes a row and returns a normalized one.  This is
        chosen at time of the first call.  All subsequent calls should use the
        same kind of row.
    nothings : dict
        Maps column name to the placeholder value to use if that column is
        missing.
    """

    def __init__(self, columns, style):
        self._columns = columns
        self.method = None

        self.delayed = defaultdict(list)
        self.delayed_columns = set()
        self.nothings = {}  # column => missing value

        for column in columns:
            cstyle = style[column]

            if "delayed" in cstyle:
                value = cstyle["delayed"]
                group = column if value is True else value
                self.delayed[group].append(column)
                self.delayed_columns.add(column)

            if "missing" in cstyle:
                self.nothings[column] = Nothing(cstyle["missing"])
            else:
                self.nothings[column] = NOTHING

    def __call__(self, row):
        """Normalize `row`

        Parameters
        ----------
        row : mapping, sequence, or other
            Data to normalize.

        Returns
        -------
        A tuple (callables, row), where `callables` is a list (as returned by
        `strip_callables`) and `row` is the normalized row.
        """
        if self.method is None:
            self.method = self._choose_normalizer(row)
        return self.method(row)

    def _choose_normalizer(self, row):
        if isinstance(row, Mapping):
            getter = self.getter_dict
        elif isinstance(row, Sequence):
            getter = self.getter_seq
        else:
            getter = self.getter_attrs
        return partial(self._normalize, getter)

    def _normalize(self, getter, row):
        if isinstance(row, Mapping):
            callables0 = self.strip_callables(row)
        else:
            callables0 = []

        norm_row = self._maybe_delay(getter, row, self._columns)
        # We need a second pass with strip_callables because norm_row will
        # contain new callables for any delayed values.
        callables1 = self.strip_callables(norm_row)
        return callables0 + callables1, norm_row

    def _maybe_delay(self, getter, row, columns):
        row_norm = {}
        for column in columns:
            if column not in self.delayed_columns:
                row_norm[column] = getter(row, column)

        def delay(cols):
            return lambda: {c: getter(row, c) for c in cols}

        for columns in self.delayed.values():
            key = columns[0] if len(columns) == 1 else tuple(columns)
            row_norm[key] = delay(columns)
        return row_norm

    @staticmethod
    def strip_callables(row):
        """Extract callable values from `row`.

        Replace the callable values with the initial value (if specified) or
        an empty string.

        Parameters
        ----------
        row : mapping
            A data row.  The keys are either a single column name or a tuple of
            column names.  The values take one of three forms: 1) a
            non-callable value, 2) a tuple (initial_value, callable), 3) or a
            single callable (in which case the initial value is set to an empty
            string).

        Returns
        -------
        list of (column, callable)
        """
        callables = []
        to_delete = []
        to_add = []
        for columns, value in row.items():
            if isinstance(value, tuple):
                initial, fn = value
            else:
                initial = NOTHING
                # Value could be a normal (non-callable) value or a
                # callable with no initial value.
                fn = value

            if callable(fn) or inspect.isgenerator(fn):
                if not isinstance(columns, tuple):
                    columns = columns,
                else:
                    to_delete.append(columns)
                for column in columns:
                    to_add.append((column, initial))
                callables.append((columns, fn))

        for column, value in to_add:
            row[column] = value
        for multi_columns in to_delete:
            del row[multi_columns]

        return callables

    # Input-specific getters.  These exist as their own methods so that they
    # can be wrapped in a callable and delayed.

    def getter_dict(self, row, column):
        return row.get(column, self.nothings[column])

    def getter_seq(self, row, column):
        col_to_idx = {c: idx for idx, c in enumerate(self._columns)}
        return row[col_to_idx[column]]

    def getter_attrs(self, row, column):
        return getattr(row, column, self.nothings[column])


def _safe_get(mapping, key, default=None):
    """Helper for accessing style values.

    It exists to avoid checking whether `mapping` is indeed a mapping before
    trying to get a key.  In the context of style dicts, this eliminates "is
    this a mapping" checks in two common situations: 1) a style argument is
    None, and 2) a style key's value (e.g., width) can be either a mapping or a
    plain value.
    """
    try:
        return mapping.get(key, default)
    except AttributeError:
        return default


class StyleFields(object):
    """Generate Fields based on the specified style and processors.

    Parameters
    ----------
    style : dict
        A style that follows the schema defined in pyout.elements.
    procgen : StyleProcessors instance
        This instance is used to generate the fields from `style`.
    """

    _header_attributes = {"align", "width"}

    def __init__(self, style, procgen):
        self.init_style = style
        self.procgen = procgen

        self.style = None
        self.columns = None
        self.autowidth_columns = {}

        self.fields = None

    def build(self, columns):
        """Build the style and fields.

        Parameters
        ----------
        columns : list of str
            Column names.
        """
        self.columns = columns
        default = dict(elements.default("default_"),
                       **_safe_get(self.init_style, "default_", {}))
        self.style = elements.adopt({c: default for c in columns},
                                    self.init_style)

        hstyle = None
        if self.init_style is not None and "header_" in self.init_style:
            hstyle = {}
            for col in columns:
                cstyle = {k: v for k, v in self.style[col].items()
                          if k in self._header_attributes}
                hstyle[col] = dict(cstyle, **self.init_style["header_"])

        # Store special keys in _style so that they can be validated.
        self.style["default_"] = default
        self.style["header_"] = hstyle
        self.style["separator_"] = _safe_get(self.init_style, "separator_",
                                             elements.default("separator_"))
        elements.validate(self.style)
        self._setup_fields()

    def _setup_fields(self):
        self.fields = {}
        for column in self.columns:
            cstyle = self.style[column]

            core_procs = []
            style_width = cstyle["width"]
            is_auto = style_width == "auto" or _safe_get(style_width, "auto")

            if is_auto:
                width = _safe_get(style_width, "min", 0)
                wmax = _safe_get(style_width, "max")

                self.autowidth_columns[column] = {"max": wmax}

                if wmax is not None:
                    marker = _safe_get(style_width, "marker", True)
                    core_procs = [self.procgen.truncate(wmax, marker)]
            elif is_auto is False:
                raise ValueError("No 'width' specified")
            else:
                width = style_width
                core_procs = [self.procgen.truncate(width)]

            # We are creating a distinction between "core" processors, that we
            # always want to be active and "default" processors that we want to
            # be active unless there's an overriding style (i.e., a header is
            # being written or the `style` argument to __call__ is specified).
            field = Field(width=width, align=cstyle["align"],
                          default_keys=["core", "default"],
                          other_keys=["override"])
            field.add("pre", "default",
                      *(self.procgen.pre_from_style(cstyle)))
            field.add("post", "core", *core_procs)
            field.add("post", "default",
                      *(self.procgen.post_from_style(cstyle)))
            self.fields[column] = field

    @property
    def has_header(self):
        """Whether the style specifies that a header.
        """
        return self.style["header_"] is not None

    def _set_widths(self, row, proc_group):
        """Update auto-width Fields based on `row`.

        Parameters
        ----------
        row : dict
        proc_group : {'default', 'override'}
            Whether to consider 'default' or 'override' key for pre- and
            post-format processors.

        Returns
        -------
        True if any widths required adjustment.
        """
        adjusted = False
        for column in self.columns:
            if column in self.autowidth_columns:
                field = self.fields[column]
                # If we've added any style transform functions as
                # pre-format processors, we want to measure the width
                # of their result rather than the raw value.
                if field.pre[proc_group]:
                    value = field(row[column], keys=[proc_group],
                                  exclude_post=True)
                else:
                    value = row[column]
                value_width = len(str(value))
                wmax = self.autowidth_columns[column]["max"]
                if value_width > field.width:
                    if wmax is None or field.width < wmax:
                        adjusted = True
                    field.width = value_width
        return adjusted

    def _proc_group(self, style, adopt=True):
        """Return whether group is "default" or "override".

        In the case of "override", the self.fields pre-format and post-format
        processors will be set under the "override" key.

        Parameters
        ----------
        style : dict
            A style that follows the schema defined in pyout.elements.
        adopt : bool, optional
            Merge `self.style` and `style`, giving priority to the latter's
            keys when there are conflicts.  If False, treat `style` as a
            standalone style.
        """
        fields = self.fields
        if style is not None:
            if adopt:
                style = elements.adopt(self.style, style)
            elements.validate(style)

            for column in self.columns:
                fields[column].add(
                    "pre", "override",
                    *(self.procgen.pre_from_style(style[column])))
                fields[column].add(
                    "post", "override",
                    *(self.procgen.post_from_style(style[column])))
            return "override"
        else:
            return "default"

    def render(self, row, style=None, adopt=True):
        """Render fields with values from `row`.

        Parameters
        ----------
        row : dict
            A normalized row.
        style : dict, optional
            A style that follows the schema defined in pyout.elements.  If
            None, `self.style` is used.
        adopt : bool, optional
            Merge `self.style` and `style`, using the latter's keys when there
            are conflicts matching keys.  If False, treat `style` as a
            standalone style.

        Returns
        -------
        A tuple with the rendered value (str) and a flag that indicates whether
        the field widths required adjustment (bool).
        """
        group = self._proc_group(style, adopt=adopt)
        if group == "override":
            # Override the "default" processor key.
            proc_keys = ["core", "override"]
        else:
            # Use the set of processors defined by _setup_fields.
            proc_keys = None

        adjusted = self._set_widths(row, group)
        proc_fields = [self.fields[c](row[c], keys=proc_keys)
                       for c in self.columns]
        return self.style["separator_"].join(proc_fields) + "\n", adjusted