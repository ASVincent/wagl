#!/usr/bin/env python
"""
Digital Surface Model Data extraction and smoothing.
"""

from __future__ import absolute_import, print_function
import numpy
from scipy import ndimage
import h5py
from rasterio.warp import Resampling
from wagl.constants import DatasetName, GroupName
from wagl.margins import pixel_buffer
from wagl.geobox import GriddedGeoBox
from wagl.data import reproject_file_to_array
from wagl.hdf5 import H5CompressionFilter, attach_image_attributes


def filter_dsm(array):
    """
    Applies a gaussian filter to array.

    :param array:
        A 2D NumPy array.

    :return:
        A 2D NumPy array.
    """
    # Define the kernel
    kernel = [0.009511, 0.078501, 0.009511, 0.078501, 0.647954, 0.078501,
              0.009511, 0.078501, 0.009511]
    kernel = numpy.array(kernel).reshape((3, 3))

    filtered = ndimage.convolve(array, kernel)
    return filtered


def _get_dsm(acquisition, national_dsm, buffer_distance, out_fname,
             compression=H5CompressionFilter.LZF, filter_opts=None):
    """
    A private wrapper for dealing with the internal custom workings of the
    NBAR workflow.
    """
    with h5py.File(out_fname, 'w') as fid:
        get_dsm(acquisition, national_dsm, buffer_distance, fid, compression,
                filter_opts)


def get_dsm(acquisition, national_dsm, buffer_distance=8000, out_group=None,
            compression=H5CompressionFilter.LZF, filter_opts=None):
    """
    Given an acquisition and a national Digitial Surface Model,
    extract a subset from the DSM based on the acquisition extents
    plus an x & y margins. The subset is then smoothed with a 3x3
    gaussian filter.
    A square margins is applied to the extents.

    :param acquisition:
        An instance of an acquisition object.

    :param national_dsm:
        A string containing the full filepath name to an image on
        disk containing national digital surface model.

    :param buffer_distance:
        A number representing the desired distance (in the same
        units as the acquisition) in which to calculate the extra
        number of pixels required to buffer an image.
        Default is 8000.

    :param out_group:
        If set to None (default) then the results will be returned
        as an in-memory hdf5 file, i.e. the `core` driver. Otherwise,
        a writeable HDF5 `Group` object.

        The dataset name will be as follows:

        * DatasetName.DSM_SMOOTHED

    :param compression:
        The compression filter to use.
        Default is H5CompressionFilter.LZF 

    :filter_opts:
        A dict of key value pairs available to the given configuration
        instance of H5CompressionFilter. For example
        H5CompressionFilter.LZF has the keywords *chunks* and *shuffle*
        available.
        Default is None, which will use the default settings for the
        chosen H5CompressionFilter instance.

    :return:
        An opened `h5py.File` object, that is either in-memory using the
        `core` driver, or on disk.
    """
    # Use the 1st acquisition to setup the geobox
    geobox = acquisition.gridded_geo_box()
    shape = geobox.get_shape_yx()

    # buffered image extents/margins
    margins = pixel_buffer(acquisition, buffer_distance)

    # Get the dimensions and geobox of the new image
    dem_cols = shape[1] + margins.left + margins.right
    dem_rows = shape[0] + margins.top + margins.bottom
    dem_shape = (dem_rows, dem_cols)
    dem_origin = geobox.convert_coordinates((0 - margins.left,
                                             0 - margins.top))
    dem_geobox = GriddedGeoBox(dem_shape, origin=dem_origin,
                               pixelsize=geobox.pixelsize,
                               crs=geobox.crs.ExportToWkt())

    # Retrive the DSM data
    dsm_data = reproject_file_to_array(national_dsm, dst_geobox=dem_geobox,
                                       resampling=Resampling.bilinear)

    # Output the reprojected result
    # Initialise the output files
    if out_group is None:
        fid = h5py.File('dsm-subset.h5', driver='core', backing_store=False)
    else:
        fid = out_group

    if filter_opts is None:
        filter_opts = {}
    else:
        filter_opts = filter_opts.copy()

    if acquisition.tile_size[0] == 1:
        filter_opts['chunks'] = (1, dem_cols)
    else:
        # TODO: rework the tiling regime for larger dsm
        # for non single row based tiles, we won't have ideal
        # matching reads for tiled processing between the acquisition
        # and the DEM
        filter_opts['chunks'] = acquisition.tile_size
    kwargs = compression.config(**filter_opts).dataset_compression_kwargs()

    group = fid.create_group(GroupName.ELEVATION_GROUP.value)

    param_grp = group.create_group('PARAMETERS')
    param_grp.attrs['left_buffer'] = margins.left
    param_grp.attrs['right_buffer'] = margins.right
    param_grp.attrs['top_buffer'] = margins.top
    param_grp.attrs['bottom_buffer'] = margins.bottom

    # dataset attributes
    attrs = {'crs_wkt': geobox.crs.ExportToWkt(),
             'geotransform': dem_geobox.transform.to_gdal()}

    # Smooth the DSM
    dsm_data = filter_dsm(dsm_data)
    dname = DatasetName.DSM_SMOOTHED.value
    out_sm_dset = group.create_dataset(dname, data=dsm_data, **kwargs)
    desc = ("A subset of a Digital Surface Model smoothed with a gaussian "
            "kernel.")
    attrs['description'] = desc
    attach_image_attributes(out_sm_dset, attrs)

    if out_group is None:
        return fid
