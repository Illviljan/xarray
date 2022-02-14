import itertools
import textwrap
import warnings
from datetime import datetime
from inspect import getfullargspec
from typing import Any, Iterable, Mapping, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from ..core.options import OPTIONS
from ..core.pycompat import DuckArrayModule
from ..core.utils import is_scalar

try:
    import nc_time_axis  # noqa: F401

    nc_time_axis_available = True
except ImportError:
    nc_time_axis_available = False


try:
    import cftime
except ImportError:
    cftime = None

ROBUST_PERCENTILE = 2.0

# copied from seaborn
_MARKERSIZE_RANGE = np.array([18.0, 72.0])
_LINEWIDTH_RANGE = np.array([1.5, 6.0])


def import_matplotlib_pyplot():
    """import pyplot"""
    # TODO: This function doesn't do anything (after #6109), remove it?
    import matplotlib.pyplot as plt

    return plt


def _determine_extend(calc_data, vmin, vmax):
    extend_min = calc_data.min() < vmin
    extend_max = calc_data.max() > vmax
    if extend_min and extend_max:
        return "both"
    elif extend_min:
        return "min"
    elif extend_max:
        return "max"
    else:
        return "neither"


def _build_discrete_cmap(cmap, levels, extend, filled):
    """
    Build a discrete colormap and normalization of the data.
    """
    import matplotlib as mpl

    if len(levels) == 1:
        levels = [levels[0], levels[0]]

    if not filled:
        # non-filled contour plots
        extend = "max"

    if extend == "both":
        ext_n = 2
    elif extend in ["min", "max"]:
        ext_n = 1
    else:
        ext_n = 0

    n_colors = len(levels) + ext_n - 1
    pal = _color_palette(cmap, n_colors)

    new_cmap, cnorm = mpl.colors.from_levels_and_colors(levels, pal, extend=extend)
    # copy the old cmap name, for easier testing
    new_cmap.name = getattr(cmap, "name", cmap)

    # copy colors to use for bad, under, and over values in case they have been
    # set to non-default values
    try:
        # matplotlib<3.2 only uses bad color for masked values
        bad = cmap(np.ma.masked_invalid([np.nan]))[0]
    except TypeError:
        # cmap was a str or list rather than a color-map object, so there are
        # no bad, under or over values to check or copy
        pass
    else:
        under = cmap(-np.inf)
        over = cmap(np.inf)

        new_cmap.set_bad(bad)

        # Only update under and over if they were explicitly changed by the user
        # (i.e. are different from the lowest or highest values in cmap). Otherwise
        # leave unchanged so new_cmap uses its default values (its own lowest and
        # highest values).
        if under != cmap(0):
            new_cmap.set_under(under)
        if over != cmap(cmap.N - 1):
            new_cmap.set_over(over)

    return new_cmap, cnorm


def _color_palette(cmap, n_colors):
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap

    colors_i = np.linspace(0, 1.0, n_colors)
    if isinstance(cmap, (list, tuple)):
        # we have a list of colors
        cmap = ListedColormap(cmap, N=n_colors)
        pal = cmap(colors_i)
    elif isinstance(cmap, str):
        # we have some sort of named palette
        try:
            # is this a matplotlib cmap?
            cmap = plt.get_cmap(cmap)
            pal = cmap(colors_i)
        except ValueError:
            # ValueError happens when mpl doesn't like a colormap, try seaborn
            try:
                from seaborn import color_palette

                pal = color_palette(cmap, n_colors=n_colors)
            except (ValueError, ImportError):
                # or maybe we just got a single color as a string
                cmap = ListedColormap([cmap], N=n_colors)
                pal = cmap(colors_i)
    else:
        # cmap better be a LinearSegmentedColormap (e.g. viridis)
        pal = cmap(colors_i)

    return pal


# _determine_cmap_params is adapted from Seaborn:
# https://github.com/mwaskom/seaborn/blob/v0.6/seaborn/matrix.py#L158
# Used under the terms of Seaborn's license, see licenses/SEABORN_LICENSE.


def _determine_cmap_params(
    plot_data,
    vmin=None,
    vmax=None,
    cmap=None,
    center=None,
    robust=False,
    extend=None,
    levels=None,
    filled=True,
    norm=None,
    _is_facetgrid=False,
):
    """
    Use some heuristics to set good defaults for colorbar and range.

    Parameters
    ----------
    plot_data : Numpy array
        Doesn't handle xarray objects

    Returns
    -------
    cmap_params : dict
        Use depends on the type of the plotting function
    """
    import matplotlib as mpl

    if isinstance(levels, Iterable):
        levels = sorted(levels)

    calc_data = np.ravel(plot_data[np.isfinite(plot_data)])

    # Handle all-NaN input data gracefully
    if calc_data.size == 0:
        # Arbitrary default for when all values are NaN
        calc_data = np.array(0.0)

    # Setting center=False prevents a divergent cmap
    possibly_divergent = center is not False

    # Set center to 0 so math below makes sense but remember its state
    center_is_none = False
    if center is None:
        center = 0
        center_is_none = True

    # Setting both vmin and vmax prevents a divergent cmap
    if (vmin is not None) and (vmax is not None):
        possibly_divergent = False

    # Setting vmin or vmax implies linspaced levels
    user_minmax = (vmin is not None) or (vmax is not None)

    # vlim might be computed below
    vlim = None

    # save state; needed later
    vmin_was_none = vmin is None
    vmax_was_none = vmax is None

    if vmin is None:
        if robust:
            vmin = np.percentile(calc_data, ROBUST_PERCENTILE)
        else:
            vmin = calc_data.min()
    elif possibly_divergent:
        vlim = abs(vmin - center)

    if vmax is None:
        if robust:
            vmax = np.percentile(calc_data, 100 - ROBUST_PERCENTILE)
        else:
            vmax = calc_data.max()
    elif possibly_divergent:
        vlim = abs(vmax - center)

    if possibly_divergent:
        levels_are_divergent = (
            isinstance(levels, Iterable) and levels[0] * levels[-1] < 0
        )
        # kwargs not specific about divergent or not: infer defaults from data
        divergent = (
            ((vmin < 0) and (vmax > 0)) or not center_is_none or levels_are_divergent
        )
    else:
        divergent = False

    # A divergent map should be symmetric around the center value
    if divergent:
        if vlim is None:
            vlim = max(abs(vmin - center), abs(vmax - center))
        vmin, vmax = -vlim, vlim

    # Now add in the centering value and set the limits
    vmin += center
    vmax += center

    # now check norm and harmonize with vmin, vmax
    if norm is not None:
        if norm.vmin is None:
            norm.vmin = vmin
        else:
            if not vmin_was_none and vmin != norm.vmin:
                raise ValueError("Cannot supply vmin and a norm with a different vmin.")
            vmin = norm.vmin

        if norm.vmax is None:
            norm.vmax = vmax
        else:
            if not vmax_was_none and vmax != norm.vmax:
                raise ValueError("Cannot supply vmax and a norm with a different vmax.")
            vmax = norm.vmax

    # if BoundaryNorm, then set levels
    if isinstance(norm, mpl.colors.BoundaryNorm):
        levels = norm.boundaries

    # Choose default colormaps if not provided
    if cmap is None:
        if divergent:
            cmap = OPTIONS["cmap_divergent"]
        else:
            cmap = OPTIONS["cmap_sequential"]

    # Handle discrete levels
    if levels is not None:
        if is_scalar(levels):
            if user_minmax:
                levels = np.linspace(vmin, vmax, levels)
            elif levels == 1:
                levels = np.asarray([(vmin + vmax) / 2])
            else:
                # N in MaxNLocator refers to bins, not ticks
                ticker = mpl.ticker.MaxNLocator(levels - 1)
                levels = ticker.tick_values(vmin, vmax)
        vmin, vmax = levels[0], levels[-1]

    # GH3734
    if vmin == vmax:
        vmin, vmax = mpl.ticker.LinearLocator(2).tick_values(vmin, vmax)

    if extend is None:
        extend = _determine_extend(calc_data, vmin, vmax)

    if levels is not None or isinstance(norm, mpl.colors.BoundaryNorm):
        cmap, newnorm = _build_discrete_cmap(cmap, levels, extend, filled)
        norm = newnorm if norm is None else norm

    # vmin & vmax needs to be None if norm is passed
    # TODO: always return a norm with vmin and vmax
    if norm is not None:
        vmin = None
        vmax = None

    return dict(
        vmin=vmin, vmax=vmax, cmap=cmap, extend=extend, levels=levels, norm=norm
    )


def _infer_xy_labels_3d(darray, x, y, rgb):
    """
    Determine x and y labels for showing RGB images.

    Attempts to infer which dimension is RGB/RGBA by size and order of dims.

    """
    assert rgb is None or rgb != x
    assert rgb is None or rgb != y
    # Start by detecting and reporting invalid combinations of arguments
    assert darray.ndim == 3
    not_none = [a for a in (x, y, rgb) if a is not None]
    if len(set(not_none)) < len(not_none):
        raise ValueError(
            "Dimension names must be None or unique strings, but imshow was "
            f"passed x={x!r}, y={y!r}, and rgb={rgb!r}."
        )
    for label in not_none:
        if label not in darray.dims:
            raise ValueError(f"{label!r} is not a dimension")

    # Then calculate rgb dimension if certain and check validity
    could_be_color = [
        label
        for label in darray.dims
        if darray[label].size in (3, 4) and label not in (x, y)
    ]
    if rgb is None and not could_be_color:
        raise ValueError(
            "A 3-dimensional array was passed to imshow(), but there is no "
            "dimension that could be color.  At least one dimension must be "
            "of size 3 (RGB) or 4 (RGBA), and not given as x or y."
        )
    if rgb is None and len(could_be_color) == 1:
        rgb = could_be_color[0]
    if rgb is not None and darray[rgb].size not in (3, 4):
        raise ValueError(
            f"Cannot interpret dim {rgb!r} of size {darray[rgb].size} as RGB or RGBA."
        )

    # If rgb dimension is still unknown, there must be two or three dimensions
    # in could_be_color.  We therefore warn, and use a heuristic to break ties.
    if rgb is None:
        assert len(could_be_color) in (2, 3)
        rgb = could_be_color[-1]
        warnings.warn(
            "Several dimensions of this array could be colors.  Xarray "
            f"will use the last possible dimension ({rgb!r}) to match "
            "matplotlib.pyplot.imshow.  You can pass names of x, y, "
            "and/or rgb dimensions to override this guess."
        )
    assert rgb is not None

    # Finally, we pick out the red slice and delegate to the 2D version:
    return _infer_xy_labels(darray.isel(**{rgb: 0}), x, y)


def _infer_xy_labels(darray, x, y, imshow=False, rgb=None):
    """
    Determine x and y labels. For use in _plot2d

    darray must be a 2 dimensional data array, or 3d for imshow only.
    """
    if (x is not None) and (x == y):
        raise ValueError("x and y cannot be equal.")

    if imshow and darray.ndim == 3:
        return _infer_xy_labels_3d(darray, x, y, rgb)

    if x is None and y is None:
        if darray.ndim != 2:
            raise ValueError("DataArray must be 2d")
        y, x = darray.dims
    elif x is None:
        _assert_valid_xy(darray, y, "y")
        x = darray.dims[0] if y == darray.dims[1] else darray.dims[1]
    elif y is None:
        _assert_valid_xy(darray, x, "x")
        y = darray.dims[0] if x == darray.dims[1] else darray.dims[1]
    else:
        _assert_valid_xy(darray, x, "x")
        _assert_valid_xy(darray, y, "y")

        if (
            all(k in darray._level_coords for k in (x, y))
            and darray._level_coords[x] == darray._level_coords[y]
        ):
            raise ValueError("x and y cannot be levels of the same MultiIndex")

    return x, y


# TODO: Can by used to more than x or y, rename?
def _assert_valid_xy(darray, xy, name):
    """
    make sure x and y passed to plotting functions are valid
    """

    # MultiIndex cannot be plotted; no point in allowing them here
    multiindex = {darray._level_coords[lc] for lc in darray._level_coords}

    valid_xy = set(darray.dims) | set(darray.coords) | set(darray._level_coords)
    valid_xy -= multiindex

    if (xy is not None) and (xy not in valid_xy):
        valid_xy_str = "', '".join(sorted(valid_xy))
        raise ValueError(f"{name} must be one of None, '{valid_xy_str}', got '{xy}'.")


def get_axis(figsize=None, size=None, aspect=None, ax=None, **kwargs):
    try:
        import matplotlib as mpl
        import matplotlib.pyplot as plt
    except ImportError:
        raise ImportError("matplotlib is required for plot.utils.get_axis")

    if figsize is not None:
        if ax is not None:
            raise ValueError("cannot provide both `figsize` and `ax` arguments")
        if size is not None:
            raise ValueError("cannot provide both `figsize` and `size` arguments")
        _, ax = plt.subplots(figsize=figsize)
    elif size is not None:
        if ax is not None:
            raise ValueError("cannot provide both `size` and `ax` arguments")
        if aspect is None:
            width, height = mpl.rcParams["figure.figsize"]
            aspect = width / height
        figsize = (size * aspect, size)
        _, ax = plt.subplots(figsize=figsize)
    elif aspect is not None:
        raise ValueError("cannot provide `aspect` argument without `size`")

    if kwargs and ax is not None:
        raise ValueError("cannot use subplot_kws with existing ax")

    if ax is None:
        ax = _maybe_gca(**kwargs)

    return ax


def _maybe_gca(**kwargs):

    import matplotlib.pyplot as plt

    # can call gcf unconditionally: either it exists or would be created by plt.axes
    f = plt.gcf()

    # only call gca if an active axes exists
    if f.axes:
        # can not pass kwargs to active axes
        return plt.gca()

    return plt.axes(**kwargs)


def _get_units_from_attrs(da):
    """Extracts and formats the unit/units from a attributes."""
    pint_array_type = DuckArrayModule("pint").type
    units = " [{}]"
    if isinstance(da.data, pint_array_type):
        units = units.format(str(da.data.units))
    elif da.attrs.get("units"):
        units = units.format(da.attrs["units"])
    elif da.attrs.get("unit"):
        units = units.format(da.attrs["unit"])
    else:
        units = ""
    return units


def label_from_attrs(da, extra=""):
    """Makes informative labels if variable metadata (attrs) follows
    CF conventions."""

    if da.attrs.get("long_name"):
        name = da.attrs["long_name"]
    elif da.attrs.get("standard_name"):
        name = da.attrs["standard_name"]
    elif da.name is not None:
        name = da.name
    else:
        name = ""

    units = _get_units_from_attrs(da)

    # Treat `name` differently if it's a latex sequence
    if name.startswith("$") and (name.count("$") % 2 == 0):
        return "$\n$".join(
            textwrap.wrap(name + extra + units, 60, break_long_words=False)
        )
    else:
        return "\n".join(textwrap.wrap(name + extra + units, 30))


def _interval_to_mid_points(array):
    """
    Helper function which returns an array
    with the Intervals' mid points.
    """

    return np.array([x.mid for x in array])


def _interval_to_bound_points(array):
    """
    Helper function which returns an array
    with the Intervals' boundaries.
    """

    array_boundaries = np.array([x.left for x in array])
    array_boundaries = np.concatenate((array_boundaries, np.array([array[-1].right])))

    return array_boundaries


def _interval_to_double_bound_points(xarray, yarray):
    """
    Helper function to deal with a xarray consisting of pd.Intervals. Each
    interval is replaced with both boundaries. I.e. the length of xarray
    doubles. yarray is modified so it matches the new shape of xarray.
    """

    xarray1 = np.array([x.left for x in xarray])
    xarray2 = np.array([x.right for x in xarray])

    xarray = list(itertools.chain.from_iterable(zip(xarray1, xarray2)))
    yarray = list(itertools.chain.from_iterable(zip(yarray, yarray)))

    return xarray, yarray


def _resolve_intervals_1dplot(xval, yval, kwargs):
    """
    Helper function to replace the values of x and/or y coordinate arrays
    containing pd.Interval with their mid-points or - for step plots - double
    points which double the length.
    """
    x_suffix = ""
    y_suffix = ""

    # Is it a step plot? (see matplotlib.Axes.step)
    if kwargs.get("drawstyle", "").startswith("steps-"):

        remove_drawstyle = False
        # Convert intervals to double points
        if _valid_other_type(np.array([xval, yval]), [pd.Interval]):
            raise TypeError("Can't step plot intervals against intervals.")
        if _valid_other_type(xval, [pd.Interval]):
            xval, yval = _interval_to_double_bound_points(xval, yval)
            remove_drawstyle = True
        if _valid_other_type(yval, [pd.Interval]):
            yval, xval = _interval_to_double_bound_points(yval, xval)
            remove_drawstyle = True

        # Remove steps-* to be sure that matplotlib is not confused
        if remove_drawstyle:
            del kwargs["drawstyle"]

    # Is it another kind of plot?
    else:

        # Convert intervals to mid points and adjust labels
        if _valid_other_type(xval, [pd.Interval]):
            xval = _interval_to_mid_points(xval)
            x_suffix = "_center"
        if _valid_other_type(yval, [pd.Interval]):
            yval = _interval_to_mid_points(yval)
            y_suffix = "_center"

    # return converted arguments
    return xval, yval, x_suffix, y_suffix, kwargs


def _resolve_intervals_2dplot(val, func_name):
    """
    Helper function to replace the values of a coordinate array containing
    pd.Interval with their mid-points or - for pcolormesh - boundaries which
    increases length by 1.
    """
    label_extra = ""
    if _valid_other_type(val, [pd.Interval]):
        if func_name == "pcolormesh":
            val = _interval_to_bound_points(val)
        else:
            val = _interval_to_mid_points(val)
            label_extra = "_center"

    return val, label_extra


def _valid_other_type(x, types):
    """
    Do all elements of x have a type from types?
    """
    return all(any(isinstance(el, t) for t in types) for el in np.ravel(x))


def _valid_numpy_subdtype(x, numpy_types):
    """
    Is any dtype from numpy_types superior to the dtype of x?
    """
    # If any of the types given in numpy_types is understood as numpy.generic,
    # all possible x will be considered valid.  This is probably unwanted.
    for t in numpy_types:
        assert not np.issubdtype(np.generic, t)

    return any(np.issubdtype(x.dtype, t) for t in numpy_types)


def _ensure_plottable(*args):
    """
    Raise exception if there is anything in args that can't be plotted on an
    axis by matplotlib.
    """
    numpy_types = [
        np.floating,
        np.integer,
        np.timedelta64,
        np.datetime64,
        np.bool_,
        np.str_,
    ]
    other_types = [datetime]
    if cftime is not None:
        cftime_datetime_types = [cftime.datetime]
        other_types = other_types + cftime_datetime_types
    else:
        cftime_datetime_types = []
    for x in args:
        if not (
            _valid_numpy_subdtype(np.array(x), numpy_types)
            or _valid_other_type(np.array(x), other_types)
        ):
            raise TypeError(
                "Plotting requires coordinates to be numeric, boolean, "
                "or dates of type numpy.datetime64, "
                "datetime.datetime, cftime.datetime or "
                f"pandas.Interval. Received data of type {np.array(x).dtype} instead."
            )
        if (
            _valid_other_type(np.array(x), cftime_datetime_types)
            and not nc_time_axis_available
        ):
            raise ImportError(
                "Plotting of arrays of cftime.datetime "
                "objects or arrays indexed by "
                "cftime.datetime objects requires the "
                "optional `nc-time-axis` (v1.2.0 or later) "
                "package."
            )


def _is_numeric(arr):
    numpy_types = [np.floating, np.integer]
    return _valid_numpy_subdtype(arr, numpy_types)


def _add_colorbar(primitive, ax, cbar_ax, cbar_kwargs, cmap_params):

    cbar_kwargs.setdefault("extend", cmap_params["extend"])
    if cbar_ax is None:
        cbar_kwargs.setdefault("ax", ax)
    else:
        cbar_kwargs.setdefault("cax", cbar_ax)

    # dont pass extend as kwarg if it is in the mappable
    if hasattr(primitive, "extend"):
        cbar_kwargs.pop("extend")

    fig = ax.get_figure()
    cbar = fig.colorbar(primitive, **cbar_kwargs)

    return cbar


def _rescale_imshow_rgb(darray, vmin, vmax, robust):
    assert robust or vmin is not None or vmax is not None

    # Calculate vmin and vmax automatically for `robust=True`
    if robust:
        if vmax is None:
            vmax = np.nanpercentile(darray, 100 - ROBUST_PERCENTILE)
        if vmin is None:
            vmin = np.nanpercentile(darray, ROBUST_PERCENTILE)
    # If not robust and one bound is None, calculate the default other bound
    # and check that an interval between them exists.
    elif vmax is None:
        vmax = 255 if np.issubdtype(darray.dtype, np.integer) else 1
        if vmax < vmin:
            raise ValueError(
                f"vmin={vmin!r} is less than the default vmax ({vmax!r}) - you must supply "
                "a vmax > vmin in this case."
            )
    elif vmin is None:
        vmin = 0
        if vmin > vmax:
            raise ValueError(
                f"vmax={vmax!r} is less than the default vmin (0) - you must supply "
                "a vmin < vmax in this case."
            )
    # Scale interval [vmin .. vmax] to [0 .. 1], with darray as 64-bit float
    # to avoid precision loss, integer over/underflow, etc with extreme inputs.
    # After scaling, downcast to 32-bit float.  This substantially reduces
    # memory usage after we hand `darray` off to matplotlib.
    darray = ((darray.astype("f8") - vmin) / (vmax - vmin)).astype("f4")
    return np.minimum(np.maximum(darray, 0), 1)


def _update_axes(
    ax,
    xincrease,
    yincrease,
    xscale=None,
    yscale=None,
    xticks=None,
    yticks=None,
    xlim=None,
    ylim=None,
):
    """
    Update axes with provided parameters
    """
    if xincrease is None:
        pass
    elif xincrease and ax.xaxis_inverted():
        ax.invert_xaxis()
    elif not xincrease and not ax.xaxis_inverted():
        ax.invert_xaxis()

    if yincrease is None:
        pass
    elif yincrease and ax.yaxis_inverted():
        ax.invert_yaxis()
    elif not yincrease and not ax.yaxis_inverted():
        ax.invert_yaxis()

    # The default xscale, yscale needs to be None.
    # If we set a scale it resets the axes formatters,
    # This means that set_xscale('linear') on a datetime axis
    # will remove the date labels. So only set the scale when explicitly
    # asked to. https://github.com/matplotlib/matplotlib/issues/8740
    if xscale is not None:
        ax.set_xscale(xscale)
    if yscale is not None:
        ax.set_yscale(yscale)

    if xticks is not None:
        ax.set_xticks(xticks)
    if yticks is not None:
        ax.set_yticks(yticks)

    if xlim is not None:
        ax.set_xlim(xlim)
    if ylim is not None:
        ax.set_ylim(ylim)


def _is_monotonic(coord, axis=0):
    """
    >>> _is_monotonic(np.array([0, 1, 2]))
    True
    >>> _is_monotonic(np.array([2, 1, 0]))
    True
    >>> _is_monotonic(np.array([0, 2, 1]))
    False
    """
    if coord.shape[axis] < 3:
        return True
    else:
        n = coord.shape[axis]
        delta_pos = coord.take(np.arange(1, n), axis=axis) >= coord.take(
            np.arange(0, n - 1), axis=axis
        )
        delta_neg = coord.take(np.arange(1, n), axis=axis) <= coord.take(
            np.arange(0, n - 1), axis=axis
        )
        return np.all(delta_pos) or np.all(delta_neg)


def _infer_interval_breaks(coord, axis=0, scale=None, check_monotonic=False):
    """
    >>> _infer_interval_breaks(np.arange(5))
    array([-0.5,  0.5,  1.5,  2.5,  3.5,  4.5])
    >>> _infer_interval_breaks([[0, 1], [3, 4]], axis=1)
    array([[-0.5,  0.5,  1.5],
           [ 2.5,  3.5,  4.5]])
    >>> _infer_interval_breaks(np.logspace(-2, 2, 5), scale="log")
    array([3.16227766e-03, 3.16227766e-02, 3.16227766e-01, 3.16227766e+00,
           3.16227766e+01, 3.16227766e+02])
    """
    coord = np.asarray(coord)

    if check_monotonic and not _is_monotonic(coord, axis=axis):
        raise ValueError(
            "The input coordinate is not sorted in increasing "
            "order along axis %d. This can lead to unexpected "
            "results. Consider calling the `sortby` method on "
            "the input DataArray. To plot data with categorical "
            "axes, consider using the `heatmap` function from "
            "the `seaborn` statistical plotting library." % axis
        )

    # If logscale, compute the intervals in the logarithmic space
    if scale == "log":
        if (coord <= 0).any():
            raise ValueError(
                "Found negative or zero value in coordinates. "
                + "Coordinates must be positive on logscale plots."
            )
        coord = np.log10(coord)

    deltas = 0.5 * np.diff(coord, axis=axis)
    if deltas.size == 0:
        deltas = np.array(0.0)
    first = np.take(coord, [0], axis=axis) - np.take(deltas, [0], axis=axis)
    last = np.take(coord, [-1], axis=axis) + np.take(deltas, [-1], axis=axis)
    trim_last = tuple(
        slice(None, -1) if n == axis else slice(None) for n in range(coord.ndim)
    )
    interval_breaks = np.concatenate(
        [first, coord[trim_last] + deltas, last], axis=axis
    )
    if scale == "log":
        # Recovert the intervals into the linear space
        return np.power(10, interval_breaks)
    return interval_breaks


def _process_cmap_cbar_kwargs(
    func,
    data,
    cmap=None,
    colors=None,
    cbar_kwargs: Union[Iterable[Tuple[str, Any]], Mapping[str, Any]] = None,
    levels=None,
    _is_facetgrid=False,
    **kwargs,
):
    """
    Parameters
    ----------
    func : plotting function
    data : ndarray,
        Data values

    Returns
    -------
    cmap_params
    cbar_kwargs
    """
    if func.__name__ == "surface":
        # Leave user to specify cmap settings for surface plots
        kwargs["cmap"] = cmap
        return {
            k: kwargs.get(k, None)
            for k in ["vmin", "vmax", "cmap", "extend", "levels", "norm"]
        }, {}

    cbar_kwargs = {} if cbar_kwargs is None else dict(cbar_kwargs)

    if "contour" in func.__name__ and levels is None:
        levels = 7  # this is the matplotlib default

    # colors is mutually exclusive with cmap
    if cmap and colors:
        raise ValueError("Can't specify both cmap and colors.")

    # colors is only valid when levels is supplied or the plot is of type
    # contour or contourf
    if colors and (("contour" not in func.__name__) and (levels is None)):
        raise ValueError("Can only specify colors with contour or levels")

    # we should not be getting a list of colors in cmap anymore
    # is there a better way to do this test?
    if isinstance(cmap, (list, tuple)):
        raise ValueError(
            "Specifying a list of colors in cmap is deprecated. "
            "Use colors keyword instead."
        )

    cmap_kwargs = {
        "plot_data": data,
        "levels": levels,
        "cmap": colors if colors else cmap,
        "filled": func.__name__ != "contour",
    }

    cmap_args = getfullargspec(_determine_cmap_params).args
    cmap_kwargs.update((a, kwargs[a]) for a in cmap_args if a in kwargs)
    if not _is_facetgrid:
        cmap_params = _determine_cmap_params(**cmap_kwargs)
    else:
        cmap_params = {
            k: cmap_kwargs[k]
            for k in ["vmin", "vmax", "cmap", "extend", "levels", "norm"]
        }

    return cmap_params, cbar_kwargs


def _get_nice_quiver_magnitude(u, v):
    import matplotlib as mpl

    ticker = mpl.ticker.MaxNLocator(3)
    mean = np.mean(np.hypot(u.to_numpy(), v.to_numpy()))
    magnitude = ticker.tick_values(0, mean)[-2]
    return magnitude


# Copied from matplotlib, tweaked so func can return strings.
# https://github.com/matplotlib/matplotlib/issues/19555
def legend_elements(
    self, prop="colors", num="auto", fmt=None, func=lambda x: x, **kwargs
):
    """
    Create legend handles and labels for a PathCollection.

    Each legend handle is a `.Line2D` representing the Path that was drawn,
    and each label is a string what each Path represents.

    This is useful for obtaining a legend for a `~.Axes.scatter` plot;
    e.g.::

        scatter = plt.scatter([1, 2, 3],  [4, 5, 6],  c=[7, 2, 3])
        plt.legend(*scatter.legend_elements())

    creates three legend elements, one for each color with the numerical
    values passed to *c* as the labels.

    Also see the :ref:`automatedlegendcreation` example.


    Parameters
    ----------
    prop : {"colors", "sizes"}, default: "colors"
        If "colors", the legend handles will show the different colors of
        the collection. If "sizes", the legend will show the different
        sizes. To set both, use *kwargs* to directly edit the `.Line2D`
        properties.
    num : int, None, "auto" (default), array-like, or `~.ticker.Locator`
        Target number of elements to create.
        If None, use all unique elements of the mappable array. If an
        integer, target to use *num* elements in the normed range.
        If *"auto"*, try to determine which option better suits the nature
        of the data.
        The number of created elements may slightly deviate from *num* due
        to a `~.ticker.Locator` being used to find useful locations.
        If a list or array, use exactly those elements for the legend.
        Finally, a `~.ticker.Locator` can be provided.
    fmt : str, `~matplotlib.ticker.Formatter`, or None (default)
        The format or formatter to use for the labels. If a string must be
        a valid input for a `~.StrMethodFormatter`. If None (the default),
        use a `~.ScalarFormatter`.
    func : function, default: ``lambda x: x``
        Function to calculate the labels.  Often the size (or color)
        argument to `~.Axes.scatter` will have been pre-processed by the
        user using a function ``s = f(x)`` to make the markers visible;
        e.g. ``size = np.log10(x)``.  Providing the inverse of this
        function here allows that pre-processing to be inverted, so that
        the legend labels have the correct values; e.g. ``func = lambda
        x: 10**x``.
    **kwargs
        Allowed keyword arguments are *color* and *size*. E.g. it may be
        useful to set the color of the markers if *prop="sizes"* is used;
        similarly to set the size of the markers if *prop="colors"* is
        used. Any further parameters are passed onto the `.Line2D`
        instance. This may be useful to e.g. specify a different
        *markeredgecolor* or *alpha* for the legend handles.

    Returns
    -------
    handles : list of `.Line2D`
        Visual representation of each element of the legend.
    labels : list of str
        The string labels for elements of the legend.
    """
    import warnings

    import matplotlib as mpl

    mlines = mpl.lines

    handles = []
    labels = []

    if prop == "colors":
        arr = self.get_array()
        if arr is None:
            warnings.warn(
                "Collection without array used. Make sure to "
                "specify the values to be colormapped via the "
                "`c` argument."
            )
            return handles, labels
        _size = kwargs.pop("size", mpl.rcParams["lines.markersize"])

        def _get_color_and_size(value):
            return self.cmap(self.norm(value)), _size

    elif prop == "sizes":
        if isinstance(self, mpl.collections.LineCollection):
            arr = self.get_linewidths()
        else:
            arr = self.get_sizes()
        _color = kwargs.pop("color", "k")

        def _get_color_and_size(value):
            return _color, np.sqrt(value)

    else:
        raise ValueError(
            "Valid values for `prop` are 'colors' or "
            f"'sizes'. You supplied '{prop}' instead."
        )

    # Get the unique values and their labels:
    values = np.unique(arr)
    label_values = np.asarray(func(values))
    label_values_are_numeric = np.issubdtype(label_values.dtype, np.number)

    # Handle the label format:
    if fmt is None and label_values_are_numeric:
        fmt = mpl.ticker.ScalarFormatter(useOffset=False, useMathText=True)
    elif fmt is None and not label_values_are_numeric:
        fmt = mpl.ticker.StrMethodFormatter("{x}")
    elif isinstance(fmt, str):
        fmt = mpl.ticker.StrMethodFormatter(fmt)
    fmt.create_dummy_axis()

    if num == "auto":
        num = 9
        if len(values) <= num:
            num = None

    if label_values_are_numeric:
        label_values_min = label_values.min()
        label_values_max = label_values.max()
        fmt.axis.set_view_interval(label_values_min, label_values_max)
        fmt.axis.set_data_interval(label_values_min, label_values_max)

        if num is not None:
            # Labels are numerical but larger than the target
            # number of elements, reduce to target using matplotlibs
            # ticker classes:
            if isinstance(num, mpl.ticker.Locator):
                loc = num
            elif np.iterable(num):
                loc = mpl.ticker.FixedLocator(num)
            else:
                num = int(num)
                loc = mpl.ticker.MaxNLocator(
                    nbins=num, min_n_ticks=num - 1, steps=[1, 2, 2.5, 3, 5, 6, 8, 10]
                )

            # Get nicely spaced label_values:
            label_values = loc.tick_values(label_values_min, label_values_max)

            # Remove extrapolated label_values:
            cond = (label_values >= label_values_min) & (
                label_values <= label_values_max
            )
            label_values = label_values[cond]

            # Get the corresponding values by creating a linear interpolant
            # with small step size:
            values_interp = np.linspace(values.min(), values.max(), 256)
            label_values_interp = func(values_interp)
            ix = np.argsort(label_values_interp)
            values = np.interp(label_values, label_values_interp[ix], values_interp[ix])
    elif num is not None and not label_values_are_numeric:
        # Labels are not numerical so modifying label_values is not
        # possible, instead filter the array with nicely distributed
        # indexes:
        if type(num) == int:
            loc = mpl.ticker.LinearLocator(num)
        else:
            raise ValueError("`num` only supports integers for non-numeric labels.")

        ind = loc.tick_values(0, len(label_values) - 1).astype(int)
        label_values = label_values[ind]
        values = values[ind]

    # Some formatters requires set_locs:
    if hasattr(fmt, "set_locs"):
        fmt.set_locs(label_values)

    # Default settings for handles, add or override with kwargs:
    kw = dict(markeredgewidth=self.get_linewidths()[0], alpha=self.get_alpha())
    kw.update(kwargs)

    for val, lab in zip(values, label_values):
        color, size = _get_color_and_size(val)

        if isinstance(self, mpl.collections.PathCollection):
            kw.update(linestyle="", marker=self.get_paths()[0], markersize=size)
        elif isinstance(self, mpl.collections.LineCollection):
            kw.update(linestyle=self.get_linestyle()[0], linewidth=size)

        h = mlines.Line2D([0], [0], color=color, **kw)

        handles.append(h)
        labels.append(fmt(lab))

    return handles, labels


def _legend_add_subtitle(handles, labels, text, ax):
    """Add a subtitle to legend handles."""
    plt = import_matplotlib_pyplot()

    if text and len(handles) > 1:
        # Create a blank handle that's not visible, the
        # invisibillity will be used to discern which are subtitles
        # or not:
        blank_handle = plt.Line2D([], [], label=text)
        blank_handle.set_visible(False)

        # Subtitles are shown first:
        handles = [blank_handle] + handles
        labels = [text] + labels

    return handles, labels


def _adjust_legend_subtitles(legend):
    """Make invisible-handle "subtitles" entries look more like titles."""
    plt = import_matplotlib_pyplot()

    # Legend title not in rcParams until 3.0
    font_size = plt.rcParams.get("legend.title_fontsize", None)
    hpackers = legend.findobj(plt.matplotlib.offsetbox.VPacker)[0].get_children()
    hpackers = [v for v in hpackers if isinstance(v, plt.matplotlib.offsetbox.HPacker)]
    for hpack in hpackers:
        areas = hpack.get_children()
        if len(areas) < 2:
            continue
        draw_area, text_area = areas

        handles = draw_area.get_children()

        # Assume that all artists that are not visible are
        # subtitles:
        if not all(artist.get_visible() for artist in handles):
            # Remove the dummy marker which will bring the text
            # more to the center:
            draw_area.set_width(0)
            for text in text_area.get_children():
                if font_size is not None:
                    # The sutbtitles should have the same font size
                    # as normal legend titles:
                    text.set_size(font_size)


# %%
class _Normalize(Sequence):
    """
    Normalize numerical or categorical values to numerical values.

    The class includes helper methods that simplifies transforming to
    and from normalized values.

    Parameters
    ----------
    data : DataArray
        DataArray to normalize.
    width : Sequence of two numbers, optional
        Normalize the data to theses min and max values.
        The default is None.
    """

    __slots__ = (
        "_data",
        "_data_is_numeric",
        "_width",
        "_unique",
        "_unique_index",
        "_unique_inverse",
        "plt",
    )

    def __init__(self, data, width=None, _is_facetgrid=False):
        self._data = data
        self._width = width if not _is_facetgrid else None
        self.plt = import_matplotlib_pyplot()

        pint_array_type = DuckArrayModule("pint").type
        to_unique = data.to_numpy() if isinstance(self._type, pint_array_type) else data
        unique, unique_inverse = np.unique(to_unique, return_inverse=True)
        self._unique = unique
        self._unique_index = np.arange(0, unique.size)
        if data is not None:
            self._unique_inverse = data.copy(data=unique_inverse.reshape(data.shape))
            self._data_is_numeric = _is_numeric(data)
        else:
            self._unique_inverse = unique_inverse
            self._data_is_numeric = False

    def __repr__(self):
        with np.printoptions(precision=4, suppress=True, threshold=5):
            return (
                f"<_Normalize(data, width={self._width})>\n"
                f"{self._unique} -> {self.values_unique}"
            )

    def __len__(self):
        return len(self._unique)

    def __getitem__(self, key):
        return self._unique[key]

    @property
    def _type(self):
        data = self.data
        return data.data if data is not None else data

    @property
    def data(self):
        return self._data

    @property
    def data_is_numeric(self) -> bool:
        """
        Check if data is numeric.

        Examples
        --------
        >>> a = xr.DataArray(["b", "a", "a", "b", "c"])
        >>> _Normalize(a).data_is_numeric
        False
        """
        return self._data_is_numeric

    def _calc_widths(self, y):
        if self._width is None or y is None:
            return y

        x0, x1 = self._width

        k = (y - np.min(y)) / (np.max(y) - np.min(y))
        widths = x0 + k * (x1 - x0)

        return widths

    def _indexes_centered(self, x):
        """
        Offset indexes to make sure being in the center of self.levels.
        ["a", "b", "c"] -> [1, 3, 5]
        """
        if self.data is None:
            return None
        else:
            return x * 2 + 1

    @property
    def values(self):
        """
        Return a normalized number array for the unique levels.

        Examples
        --------
        >>> a = xr.DataArray(["b", "a", "a", "b", "c"])
        >>> _Normalize(a).values
        <xarray.DataArray (dim_0: 5)>
        array([3, 1, 1, 3, 5])
        Dimensions without coordinates: dim_0

        >>> _Normalize(a, width=[18, 72]).values
        <xarray.DataArray (dim_0: 5)>
        array([45., 18., 18., 45., 72.])
        Dimensions without coordinates: dim_0

        >>> a = xr.DataArray([0.5, 0, 0, 0.5, 2, 3])
        >>> _Normalize(a).values
        <xarray.DataArray (dim_0: 6)>
        array([0.5, 0. , 0. , 0.5, 2. , 3. ])
        Dimensions without coordinates: dim_0

        >>> _Normalize(a, width=[18, 72]).values
        <xarray.DataArray (dim_0: 6)>
        array([27., 18., 18., 27., 54., 72.])
        Dimensions without coordinates: dim_0
        """
        return self._calc_widths(
            self.data
            if self.data_is_numeric
            else self._indexes_centered(self._unique_inverse)
        )

    def _integers(self):
        """
        Return integers.
        ["a", "b", "c"] -> [1, 3, 5]
        """
        return self._indexes_centered(self._unique_index)

    @property
    def values_unique(self):
        """
        Return unique values.

        Examples
        --------
        >>> a = xr.DataArray(["b", "a", "a", "b", "c"])
        >>> _Normalize(a).values_unique
        array([1, 3, 5])
        >>> a = xr.DataArray([2, 1, 1, 2, 3])
        >>> _Normalize(a).values_unique
        array([1, 2, 3])
        >>> _Normalize(a, width=[18, 72]).values_unique
        array([18., 45., 72.])
        """
        return (
            self._integers()
            if not self.data_is_numeric
            else self._calc_widths(self._unique)
        )

    @property
    def ticks(self):
        """
        Return ticks for plt.colorbar if the data is not numeric.

        Examples
        --------
        >>> a = xr.DataArray(["b", "a", "a", "b", "c"])
        >>> _Normalize(a).ticks
        array([1, 3, 5])
        """
        return self._integers() if not self.data_is_numeric else None

    @property
    def levels(self):
        """
        Return discrete levels that will evenly bound self.values.
        ["a", "b", "c"] -> [0, 2, 4, 6]

        Examples
        --------
        >>> a = xr.DataArray(["b", "a", "a", "b", "c"])
        >>> _Normalize(a).levels
        array([0, 2, 4, 6])
        """
        return np.append(self._unique_index, np.max(self._unique_index) + 1) * 2

    @property
    def _lookup(self) -> pd.Series:
        return pd.Series(dict(zip(self.values_unique, self._unique)))

    def _lookup_arr(self, x) -> np.ndarray:
        # Use reindex to be less sensitive to float errors. reindex only
        # works with sorted index.
        # Return as numpy array since legend_elements
        # seems to require that:
        return self._lookup.sort_index().reindex(x, method="nearest").to_numpy()

    @property
    def format(self):
        """
        Return a FuncFormatter that maps self.values elements back to
        the original value as a string. Useful with plt.colorbar.

        Examples
        --------
        >>> a = xr.DataArray([0.5, 0, 0, 0.5, 2, 3])
        >>> aa = _Normalize(a, width=[0, 1])
        >>> aa._lookup
        0.000000    0.0
        0.166667    0.5
        0.666667    2.0
        1.000000    3.0
        dtype: float64
        >>> aa.format(1)
        '3.0'
        """
        return self.plt.FuncFormatter(lambda x, pos=None: f"{self._lookup_arr([x])[0]}")

    @property
    def func(self):
        """
        Return a lambda function that maps self.values elements back to
        the original value as a numpy array. Useful with ax.legend_elements.

        Examples
        --------
        >>> a = xr.DataArray([0.5, 0, 0, 0.5, 2, 3])
        >>> aa = _Normalize(a, width=[0, 1])
        >>> aa._lookup
        0.000000    0.0
        0.166667    0.5
        0.666667    2.0
        1.000000    3.0
        dtype: float64
        >>> aa.func([0.16, 1])
        array([0.5, 3. ])
        """
        return lambda x, pos=None: self._lookup_arr(x)


def _determine_guide(
    hueplt_norm,
    sizeplt_norm,
    add_colorbar=None,
    add_legend=None,
    plotfunc_name: str = None,
):
    if plotfunc_name == "hist":
        return False, False

    if (add_colorbar) and hueplt_norm.data is None:
        raise KeyError("Cannot create a colorbar when hue is None.")
    if add_colorbar is None:
        if hueplt_norm.data is not None:
            add_colorbar = True
        else:
            add_colorbar = False

    if (add_legend) and hueplt_norm.data is None and sizeplt_norm.data is None:
        raise KeyError("Cannot create a legend when hue and markersize is None.")
    if add_legend is None:
        if (
            not add_colorbar
            and (hueplt_norm.data is not None and hueplt_norm.data_is_numeric is False)
            or sizeplt_norm.data is not None
        ):
            add_legend = True
        else:
            add_legend = False

    return add_colorbar, add_legend


def _add_legend(
    hueplt_norm: _Normalize,
    sizeplt_norm: _Normalize,
    primitive,
    ax,
    legend_ax,
    plotfunc: str,
):

    primitive = primitive if isinstance(primitive, list) else [primitive]

    handles, labels = [], []
    for huesizeplt, prop in [
        (hueplt_norm, "colors"),
        (sizeplt_norm, "sizes"),
    ]:
        if huesizeplt.data is not None:
            # Get legend handles and labels that displays the
            # values correctly. Order might be different because
            # legend_elements uses np.unique instead of pd.unique,
            # FacetGrid.add_legend might have troubles with this:
            hdl, lbl = [], []
            for p in primitive:
                h, l = legend_elements(
                    p, prop, num="auto", func=huesizeplt.func
                )
                hdl += h
                lbl += l

            # Only save unique values:
            u, ind = np.unique(lbl, return_index=True)
            ind = np.argsort(ind)
            lbl = u[ind].tolist()
            hdl = np.array(hdl)[ind].tolist()

            # Add a subtitle:
            hdl, lbl = _legend_add_subtitle(
                hdl, lbl, label_from_attrs(huesizeplt.data), ax
            )
            handles += hdl
            labels += lbl
    legend = legend_ax.legend(handles, labels, framealpha=0.5)
    _adjust_legend_subtitles(legend)

    return legend


def _infer_meta_data(ds, x, y, hue, hue_style, add_guide, funcname):
    dvars = set(ds.variables.keys())

    error_msg = f" must be one of ({', '.join(dvars)})"

    if x not in dvars:
        raise ValueError("x" + error_msg + f", got {x}")

    if y not in dvars:
        raise ValueError("y" + error_msg + f", got {y}")

    if hue is not None and hue not in dvars:
        raise ValueError("hue" + error_msg + f", got {hue}")

    if hue:
        hue_is_numeric = _is_numeric(ds[hue].values)

        if hue_style is None:
            hue_style = "continuous" if hue_is_numeric else "discrete"

        if not hue_is_numeric and (hue_style == "continuous"):
            raise ValueError(
                f"Cannot create a colorbar for a non numeric coordinate: {hue}"
            )

        if add_guide is None or add_guide is True:
            add_colorbar = True if hue_style == "continuous" else False
            add_legend = True if hue_style == "discrete" else False
        else:
            add_colorbar = False
            add_legend = False
    else:
        if add_guide is True and funcname not in ("quiver", "streamplot"):
            raise ValueError("Cannot set add_guide when hue is None.")
        add_legend = False
        add_colorbar = False

    if (add_guide or add_guide is None) and funcname == "quiver":
        add_quiverkey = True
        if hue:
            add_colorbar = True
            if not hue_style:
                hue_style = "continuous"
            elif hue_style != "continuous":
                raise ValueError(
                    "hue_style must be 'continuous' or None for .plot.quiver or "
                    ".plot.streamplot"
                )
    else:
        add_quiverkey = False

    if (add_guide or add_guide is None) and funcname == "streamplot":
        if hue:
            add_colorbar = True
            if not hue_style:
                hue_style = "continuous"
            elif hue_style != "continuous":
                raise ValueError(
                    "hue_style must be 'continuous' or None for .plot.quiver or "
                    ".plot.streamplot"
                )

    if hue_style is not None and hue_style not in ["discrete", "continuous"]:
        raise ValueError("hue_style must be either None, 'discrete' or 'continuous'.")

    if hue:
        hue_label = label_from_attrs(ds[hue])
        hue = ds[hue]
    else:
        hue_label = None
        hue = None

    return {
        "add_colorbar": add_colorbar,
        "add_legend": add_legend,
        "add_quiverkey": add_quiverkey,
        "hue_label": hue_label,
        "hue_style": hue_style,
        "xlabel": label_from_attrs(ds[x]),
        "ylabel": label_from_attrs(ds[y]),
        "hue": hue,
    }


# copied from seaborn
def _parse_size(data, norm, width):
    """
    Determine what type of data it is. Then normalize it to width.

    If the data is categorical, normalize it to numbers.
    """
    plt = import_matplotlib_pyplot()

    if data is None:
        return None

    data = data.values.ravel()

    if not _is_numeric(data):
        # Data is categorical.
        # Use pd.unique instead of np.unique because that keeps
        # the order of the labels:
        levels = pd.unique(data)
        numbers = np.arange(1, 1 + len(levels))
    else:
        levels = numbers = np.sort(np.unique(data))

    min_width, max_width = width
    # width_range = min_width, max_width

    if norm is None:
        norm = plt.Normalize()
    elif isinstance(norm, tuple):
        norm = plt.Normalize(*norm)
    elif not isinstance(norm, plt.Normalize):
        err = "``size_norm`` must be None, tuple, or Normalize object."
        raise ValueError(err)

    norm.clip = True
    if not norm.scaled():
        norm(np.asarray(numbers))
    # limits = norm.vmin, norm.vmax

    scl = norm(numbers)
    widths = np.asarray(min_width + scl * (max_width - min_width))
    if scl.mask.any():
        widths[scl.mask] = 0
    sizes = dict(zip(levels, widths))

    return pd.Series(sizes)
    return pd.Series(sizes)


def _line(
    self,
    x,
    y,
    s=None,
    c=None,
    linestyle=None,
    cmap=None,
    norm=None,
    vmin=None,
    vmax=None,
    alpha=None,
    linewidths=None,
    *,
    edgecolors=None,
    plotnonfinite=False,
    **kwargs,
):
    """
    ax.scatter-like wrapper for LineCollection.

    This function helps the handling of datetimes since Linecollection doesn't
    support it directly, just like PatchCollection doesn't either.

    """
    plt = import_matplotlib_pyplot()
    rcParams = plt.matplotlib.rcParams

    # Handle z inputs:
    z = kwargs.pop("z", None)
    if z is not None:
        from mpl_toolkits.mplot3d.art3d import Line3DCollection

        LineCollection_ = Line3DCollection
        add_collection_ = self.add_collection3d
        auto_scale = self.auto_scale_xyz
        auto_scale_args = (x, y, z, self.has_data())
    else:
        LineCollection_ = plt.matplotlib.collections.LineCollection
        add_collection_ = self.add_collection
        auto_scale = self._request_autoscale_view
        auto_scale_args = tuple()

    # Process **kwargs to handle aliases, conflicts with explicit kwargs:
    x, y = self._process_unit_info([("x", x), ("y", y)], kwargs)

    if s is None:
        s = np.array([rcParams["lines.linewidth"]])
    # s = np.ma.ravel(s)
    if len(s) not in (1, x.size) or (
        not np.issubdtype(s.dtype, np.floating)
        and not np.issubdtype(s.dtype, np.integer)
    ):
        raise ValueError(
            "s must be a scalar, " "or float array-like with the same size as x and y"
        )

    edgecolors or kwargs.get("edgecolor", None)
    c, colors, edgecolors = self._parse_scatter_color_args(
        c,
        edgecolors,
        kwargs,
        x.size,
        get_next_color_func=self._get_patches_for_fill.get_next_color,
    )

    # load default linestyle from rcParams
    if linestyle is None:
        linestyle = rcParams["lines.linestyle"]

    drawstyle = kwargs.pop("drawstyle", "default")
    if drawstyle == "default":
        # Draw linear lines:
        xyz = list(v for v in (x, y, z) if v is not None)
    else:
        # Create steps by repeating all elements, then roll the last array by 1:
        # Might be scary duplicating number of elements?
        xyz = list(np.repeat(v, 2) for v in (x, y, z) if v is not None)
        c = np.repeat(c, 2)  # TODO: Off by one?
        s = np.repeat(s, 2)
        if drawstyle == "steps-pre":
            xyz[-1][:-1] = xyz[-1][1:]
        elif drawstyle == "steps-post":
            xyz[-1][1:] = xyz[-1][:-1]
        else:
            raise NotImplementedError(
                f"Allowed values are: 'default', 'steps-pre', 'steps-post', got {drawstyle}."
            )

    # Broadcast arrays to correct format:
    # https://stackoverflow.com/questions/42215777/matplotlib-line-color-in-3d
    points = np.stack(np.broadcast_arrays(*xyz), axis=-1).reshape(-1, 1, len(xyz))
    segments = np.concatenate([points[:-1], points[1:]], axis=1)

    collection = LineCollection_(
        segments,
        linewidths=s,
        linestyles="solid",
    )
    # collection.set_transform(plt.matplotlib.transforms.IdentityTransform())
    collection.update(kwargs)

    if colors is None:
        collection.set_array(c)
        collection.set_cmap(cmap)
        collection.set_norm(norm)
        collection._scale_norm(norm, vmin, vmax)

    add_collection_(collection)

    # self._request_autoscale_view()
    # self.autoscale_view()
    auto_scale(*auto_scale_args)

    return collection
