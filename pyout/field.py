"""Define a "field" based on a sequence of processor functions.
"""


class Field(object):
    """Format, process, and render tabular fields.

    A Field instance is a template for a string that is defined by its
    width, text alignment, and its "processors".  When a field is
    called with a value, it renders the value as a string with the
    specified width and text alignment.  Before this string is
    returned, it is passed through the chain of processors.  The
    rendered string is the result returned by the last processor.

    Parameters
    ----------
    width : int
    align : {'left', 'right', 'center'}

    Attributes
    ----------
    width : int
    align : str
    processors : dict
        Each key maps to a list of processors.  The keys "core" and
        "default" must always be present.  When an instance object is
        called, the rendered result is always sent through the "core"
        processors.  It will then be sent through the "default"
        processors unless another key is provided as the optional
        `which` argument.

        A processor should take two positional arguments, the value
        that is being rendered and the current result.  Its return
        value will be passed to the next processor as the current
        result.
    """

    _align_values = {"left": "<", "right": ">", "center": "^"}

    def __init__(self, width=10, align="left"):
        self._width = width
        self._align = align
        self._fmt = self._build_format()

        self.processors = {"core": [], "default": []}

    @property
    def width(self):
        return self._width

    @width.setter
    def width(self, value):
        self._width = value
        self._fmt = self._build_format()

    def _build_format(self):
        align = self._align_values[self._align]
        return "".join(["{:", align, str(self.width), "}"])

    def __call__(self, value, which="default"):
        """Render `value` by feeding it through the processors.

        Parameters
        ----------
        value : str
        which : str, optional
            A key for the `processors` attribute that indicates the
            list of processors to use in addition to the "core" list.
        """
        result = self._fmt.format(value)
        for fn in self.processors["core"] + self.processors[which]:
            result = fn(value, result)
        return result


class StyleProcessors(object):
    """A base class for generating Field.processors for styled output.

    Attributes
    ----------
    style_keys : list of tuples
        Each pair consists of a style attribute (e.g., "bold") and the
        expected type.
    """

    style_keys = [("bold", bool),
                  ("underline", bool),
                  ("color", str)]

    def translate(self, name):
        """Translate a style key for a given output type.

        Parameters
        ----------
        name : str
            A style key (e.g., "bold").

        Returns
        -------
        An output-specific translation of `name`.
        """
        raise NotImplementedError

    @staticmethod
    def truncate(length, marker=True):
        """Return a processor that truncates the result to `length`.

        Note: You probably want to place this function at the
        beginning of the processor list so that the truncation is
        based on the length of the original value.

        Parameters
        ----------
        length : int
        marker : str or bool
            Indicate truncation with this string.  If True, indicate
            truncation by replacing the last three characters of a
            truncated string with '...'.  If False, no truncation
            marker is added to a truncated string.

        Returns
        -------
        A function.
        """
        if marker is True:
            marker = "..."

        # TODO: Add an option to center the truncation marker?
        def truncate_fn(_, result):
            if len(result) <= length:
                return result
            if marker:
                marker_beg = max(length - len(marker), 0)
                if result[marker_beg:].strip():
                    if marker_beg == 0:
                        return marker[:length]
                    return result[:marker_beg] + marker
            return result[:length]
        return truncate_fn

    def by_key(self, key):
        """Return a processor for the style given by `key`.

        Parameters
        ----------
        key : str
            A style key to be translated.

        Returns
        -------
        A function.
        """
        def by_key_fn(_, result):
            return self.translate(key) + result
        return by_key_fn

    def by_lookup(self, mapping, key=None):
        """Return a processor that extracts the style from `mapping`.

        Parameters
        ----------
        mapping : mapping
            A map from the field value to a style key, or, if `key` is
            given, a map from the field value to a value that
            indicates whether the processor should style its result.
        key : str, optional
            A style key to be translated.  If not given, the value
            from `mapping` is used.

        Returns
        -------
        A function.
        """
        def by_lookup_fn(value, result):
            try:
                lookup_value = mapping[value]
            except KeyError:
                return result

            if not lookup_value:
                return result
            return self.translate(key or lookup_value) + result
        return by_lookup_fn

    def by_interval_lookup(self, intervals, key=None):
        """Return a processor that extracts the style from `intervals`.

        Parameters
        ----------
        intervals : sequence of tuples
            Each tuple should have the form `(start, end, key)`, where
            start is the start of the interval (inclusive) , end is
            the end of the interval, and key is a style key.
        key : str, optional
            A style key to be translated.  If not given, the value
            from `mapping` is used.

        Returns
        -------
        A function.
        """
        def by_interval_lookup_fn(value, result):
            value = float(value)
            for start, end, lookup_value in intervals:
                if start is None:
                    start = float("-inf")
                elif end is None:
                    end = float("inf")

                if start <= value < end:
                    if not lookup_value:
                        return result
                    return self.translate(key or lookup_value) + result
            return result
        return by_interval_lookup_fn

    @staticmethod
    def value_type(value):
        """Classify `value` of bold, color, and underline keys.

        Parameters
        ----------
        value : style value

        Returns
        -------
        str, {"simple", "label", "interval"}
        """
        try:
            keys = list(value.keys())
        except AttributeError:
            return "simple"
        if keys in [["label"], ["interval"]]:
            return keys[0]
        raise ValueError("Type of `value` could not be determined")

    def from_style(self, column_style):
        """Yield processors based on `column_style`.

        Parameters
        ----------
        column_style : dict
            A style where the top-level keys correspond to style
            attributes such as "bold" or "color".

        Returns
        -------
        A generator object.
        """
        for key, key_type in self.style_keys:
            if key not in column_style:
                continue

            vtype = self.value_type(column_style[key])
            attr_key = key if key_type is bool else None

            if vtype == "simple":
                if key_type is bool:
                    if column_style[key] is True:
                        yield self.by_key(key)
                elif key_type is str:
                    yield self.by_key(column_style[key])
            elif vtype == "label":
                yield self.by_lookup(column_style[key][vtype], attr_key)
            elif vtype == "interval":
                yield self.by_interval_lookup(column_style[key][vtype],
                                              attr_key)