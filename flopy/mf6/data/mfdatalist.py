from collections import OrderedDict
import math
import sys
import os
import inspect
import numpy as np
from ..utils.mfenums import DiscretizationType
from ..data import mfstructure, mfdata
from ..mfbase import MFDataException, ExtFileAction, VerbosityLevel
from .mfstructure import DatumType
from ...utils import datautil
from ...datbase import DataListInterface, DataType
from ...mbase import ModelInterface
from .mffileaccess import MFFileAccessList
from .mfdatastorage import DataStorage, DataStorageType, DataStructureType
from .mfdatautil import to_string, iterable


class MFList(mfdata.MFMultiDimVar, DataListInterface):
    """
    Provides an interface for the user to access and update MODFLOW
    scalar data.

    Parameters
    ----------
    sim_data : MFSimulationData
        data contained in the simulation
    structure : MFDataStructure
        describes the structure of the data
    data : list or ndarray
        actual data
    enable : bool
        enable/disable the array
    path : tuple
        path in the data dictionary to this MFArray
    dimensions : MFDataDimensions
        dimension information related to the model, package, and array

    Attributes
    ----------
    data_type : DataType
        type of data stored in the scalar
    plottable : bool
        if the scalar is plottable
    dtype : numpy.dtype
        the scalar's numpy data type
    data : variable
        calls get_data with default parameters

    Methods
    -------
    new_simulation : (sim_data : MFSimulationData)
        initialize MFArray object for a new simulation
    has_data : (layer_num : int) : bool
        Returns whether layer "layer_num" has any data associated with it.
        For unlayered data do not pass in "layer".
    get_data : (layer_num : int) : ndarray
        Returns the data associated with layer "layer_num".  If "layer_num" is
        None, returns all data.
    set_data : (data : ndarray/list/dict, multiplier : float, layer_num : int)
        Sets the contents of the data at layer "layer_num" to "data" with
        multiplier "multiplier".  For unlayered data do not pass in
        "layer_num".  data can have the following formats:
            1) ndarray - ndarray containing the datalist
            2) [(line_one), (line_two), ...] - list where each like of the
               datalist is a tuple within the list
            3) {'filename':filename, factor=fct, iprn=print_code, data=data}
               - dictionary defining the external file containing the datalist.
        If the data is transient, a dictionary can be used to specify each
        stress period where the dictionary key is <stress period> - 1 and
        the dictionary value is the datalist data defined above:
        {0:ndarray, 1:[(line_one), (line_two), ...], 2:{'filename':filename})
    append_data : (data : list(tuple))
        Appends "data" to the end of this list.  Assumes data is in a format
        that can be appended directly to a numpy recarray.
    append_list_as_record : (data : list)
        Appends the list "data" as a single record in this list's recarray.
        Assumes "data" has the correct dimensions.
    update_record : (record : list, key_index : int)
        Updates a record at index "key_index" with the contents of "record".
        If the index does not exist update_record appends the contents of
        "record" to this list's recarray.
    search_data : (search_term : string, col : int)
        Searches the list data at column "col" for "search_term".  If col is
        None search_data searches the entire list.
    load : (first_line : string, file_handle : file descriptor,
            block_header : MFBlockHeader, pre_data_comments : MFComment) :
            tuple (bool, string)
        Loads data from first_line (the first line of data) and open file
        file_handle which is pointing to the second line of data.  Returns a
        tuple with the first item indicating whether all data was read
        and the second item being the last line of text read from the file.
    get_file_entry : (layer : int) : string
        Returns a string containing the data in layer "layer".  For unlayered
        data do not pass in "layer".
    store_as_external_file : (external_file_path : str, binary : bool)
        store all data externally in file external_file_path. the binary
        allows storage in a binary file. If replace_existing_external is set
        to False, this method will not do anything if the data is already in
        an external file.

    See Also
    --------

    Notes
    -----

    Examples
    --------


    """

    def __init__(
        self,
        sim_data,
        model_or_sim,
        structure,
        data=None,
        enable=True,
        path=None,
        dimensions=None,
        package=None,
    ):
        super(MFList, self).__init__(
            sim_data, model_or_sim, structure, enable, path, dimensions
        )
        try:
            self._data_storage = self._new_storage()
        except Exception as ex:
            type_, value_, traceback_ = sys.exc_info()
            raise MFDataException(
                structure.get_model(),
                structure.get_package(),
                path,
                "creating storage",
                structure.name,
                inspect.stack()[0][3],
                type_,
                value_,
                traceback_,
                None,
                sim_data.debug,
                ex,
            )
        self._package = package
        self._last_line_info = []
        self._data_line = None
        self._temp_dict = {}
        self._crnt_line_num = 1
        if data is not None:
            try:
                self.set_data(data, True)
            except Exception as ex:
                type_, value_, traceback_ = sys.exc_info()
                raise MFDataException(
                    structure.get_model(),
                    structure.get_package(),
                    path,
                    "setting data",
                    structure.name,
                    inspect.stack()[0][3],
                    type_,
                    value_,
                    traceback_,
                    None,
                    sim_data.debug,
                    ex,
                )

    @property
    def data_type(self):
        return DataType.list

    @property
    def package(self):
        return self._package

    @property
    def dtype(self):
        return self.get_data().dtype

    @property
    def plottable(self):
        if self.model is None:
            return False
        else:
            return True

    def to_array(self, kper=0, mask=False):
        i0 = 1
        sarr = self.get_data(key=kper)
        if not isinstance(sarr, list):
            sarr = [sarr]
        if len(sarr) == 0 or sarr[0] is None:
            return None
        if "inode" in sarr[0].dtype.names:
            raise NotImplementedError()
        arrays = {}
        model_grid = self._data_dimensions.get_model_grid()

        if model_grid._grid_type.value == 1:
            shape = (
                model_grid.num_layers(),
                model_grid.num_rows(),
                model_grid.num_columns(),
            )
        elif model_grid._grid_type.value == 2:
            shape = (
                model_grid.num_layers(),
                model_grid.num_cells_per_layer(),
            )
        else:
            shape = (model_grid.num_cells_per_layer(),)

        for name in sarr[0].dtype.names[i0:]:
            if not sarr[0].dtype.fields[name][0] == object:
                arr = np.zeros(shape)
                arrays[name] = arr.copy()

        if np.isscalar(sarr[0]):
            # if there are no entries for this kper
            if sarr[0] == 0:
                if mask:
                    for name, arr in arrays.items():
                        arrays[name][:] = np.NaN
                return arrays
            else:
                raise Exception("MfList: something bad happened")

        for name, arr in arrays.items():
            cnt = np.zeros(shape, dtype=np.float64)
            for sp_rec in sarr:
                if sp_rec is not None:
                    for rec in sp_rec:
                        arr[rec["cellid"]] += rec[name]
                        cnt[rec["cellid"]] += 1.0
            # average keys that should not be added
            if name != "cond" and name != "flux":
                idx = cnt > 0.0
                arr[idx] /= cnt[idx]
            if mask:
                arr = np.ma.masked_where(cnt == 0.0, arr)
                arr[cnt == 0.0] = np.NaN

            arrays[name] = arr.copy()
        # elif mask:
        #     for name, arr in arrays.items():
        #         arrays[name][:] = np.NaN
        return arrays

    def new_simulation(self, sim_data):
        try:
            super(MFList, self).new_simulation(sim_data)
            self._data_storage = self._new_storage()
        except Exception as ex:
            type_, value_, traceback_ = sys.exc_info()
            raise MFDataException(
                self.structure.get_model(),
                self.structure.get_package(),
                self._path,
                "reinitializing",
                self.structure.name,
                inspect.stack()[0][3],
                type_,
                value_,
                traceback_,
                None,
                self._simulation_data.debug,
                ex,
            )

        self._data_line = None

    def store_as_external_file(
        self,
        external_file_path,
        binary=False,
        replace_existing_external=True,
        check_data=True,
    ):
        # only store data externally (do not subpackage info)
        if self.structure.construct_package is None:
            storage = self._get_storage_obj()
            # check if data is already stored external
            if (
                replace_existing_external
                or storage is None
                or storage.layer_storage.first_item().data_storage_type
                == DataStorageType.internal_array
                or storage.layer_storage.first_item().data_storage_type
                == DataStorageType.internal_constant
            ):
                data = self._get_data()
                # if not empty dataset
                if data is not None:
                    if (
                        self._simulation_data.verbosity_level.value
                        >= VerbosityLevel.verbose.value
                    ):
                        print(
                            "Storing {} to external file {}.."
                            ".".format(self.structure.name, external_file_path)
                        )
                    external_data = {
                        "filename": external_file_path,
                        "data": data,
                        "binary": binary,
                    }
                    self._set_data(external_data, check_data=check_data)

    def has_data(self):
        try:
            if self._get_storage_obj() is None:
                return False
            return self._get_storage_obj().has_data()
        except Exception as ex:
            type_, value_, traceback_ = sys.exc_info()
            raise MFDataException(
                self.structure.get_model(),
                self.structure.get_package(),
                self._path,
                "checking for data",
                self.structure.name,
                inspect.stack()[0][3],
                type_,
                value_,
                traceback_,
                None,
                self._simulation_data.debug,
                ex,
            )

    def _get_data(self, apply_mult=False, **kwargs):
        try:
            if self._get_storage_obj() is None:
                return None
            return self._get_storage_obj().get_data()
        except Exception as ex:
            type_, value_, traceback_ = sys.exc_info()
            raise MFDataException(
                self.structure.get_model(),
                self.structure.get_package(),
                self._path,
                "getting data",
                self.structure.name,
                inspect.stack()[0][3],
                type_,
                value_,
                traceback_,
                None,
                self._simulation_data.debug,
                ex,
            )

    def get_data(self, apply_mult=False, **kwargs):
        return self._get_data(apply_mult, **kwargs)

    def _get_min_record_entries(self, data=None):
        try:
            if isinstance(data, dict) and "data" in data:
                data = data["data"]
            type_list = self._get_storage_obj().build_type_list(
                data=data, min_size=True
            )
        except Exception as ex:
            type_, value_, traceback_ = sys.exc_info()
            raise MFDataException(
                self.structure.get_model(),
                self.structure.get_package(),
                self._path,
                "getting min record entries",
                self.structure.name,
                inspect.stack()[0][3],
                type_,
                value_,
                traceback_,
                None,
                self._simulation_data.debug,
                ex,
            )
        return len(type_list)

    def _set_data(self, data, autofill=False, check_data=True):
        if isinstance(data, dict):
            if "data" in data:
                data_check = data["data"]
            else:
                data_check = None
        else:
            data_check = data
        if iterable(data_check) and check_data:
            # verify data length
            min_line_size = self._get_min_record_entries(data)
            if isinstance(data_check[0], np.record) or (
                iterable(data_check[0]) and not isinstance(data_check[0], str)
            ):
                # data contains multiple records
                for data_line in data_check:
                    self._check_line_size(data_line, min_line_size)
            else:
                # data is a single record
                self._check_line_size(data_check, min_line_size)
        # set data
        self._resync()
        try:
            if self._get_storage_obj() is None:
                self._data_storage = self._new_storage()
            # store data
            self._get_storage_obj().set_data(data, autofill=autofill)
        except Exception as ex:
            type_, value_, traceback_ = sys.exc_info()
            raise MFDataException(
                self.structure.get_model(),
                self.structure.get_package(),
                self._path,
                "setting data",
                self.structure.name,
                inspect.stack()[0][3],
                type_,
                value_,
                traceback_,
                None,
                self._simulation_data.debug,
                ex,
            )
        if check_data:
            # verify cellids
            self._check_valid_cellids()

    def _check_valid_cellids(self):
        # only check packages that are a part of a model
        if isinstance(self._model_or_sim, ModelInterface) and hasattr(
            self._model_or_sim, "modelgrid"
        ):
            # get model grid info
            mg = self._model_or_sim.modelgrid
            if not mg.is_complete:
                return
            idomain = mg.idomain
            model_shape = idomain.shape

            # check to see if there are any cellids
            storage_obj = self._get_storage_obj()
            if True in storage_obj.recarray_cellid_list:
                # get data
                data = storage_obj.get_data()
                # check data for invalid cellids
                for index, is_cellid in enumerate(
                    storage_obj.resolve_cellidlist(data)
                ):
                    if is_cellid:
                        for record in data:
                            if not isinstance(record[index], tuple):
                                # cellids are not always a tuple of integers,
                                # like sfr.  nothing to check in this case
                                break
                            idomain_val = idomain
                            # cellid should be within the model grid
                            for idx, cellid_part in enumerate(record[index]):
                                if (
                                    model_shape[idx] <= cellid_part
                                    or cellid_part < 0
                                ):
                                    message = (
                                        "Cellid {} is outside of the "
                                        "model grid "
                                        "{}".format(record[index], model_shape)
                                    )
                                    type_, value_, traceback_ = sys.exc_info()
                                    raise MFDataException(
                                        self.structure.get_model(),
                                        self.structure.get_package(),
                                        self.structure.path,
                                        "storing data",
                                        self.structure.name,
                                        inspect.stack()[0][3],
                                        type_,
                                        value_,
                                        traceback_,
                                        message,
                                        self._simulation_data.debug,
                                    )
                                idomain_val = idomain_val[cellid_part]
                            # cellid should be at an active cell
                            if idomain_val < 1:
                                message = (
                                    "Cellid {} is outside of the "
                                    "active model grid"
                                    ".".format(record[index])
                                )
                                type_, value_, traceback_ = sys.exc_info()
                                raise MFDataException(
                                    self.structure.get_model(),
                                    self.structure.get_package(),
                                    self.structure.path,
                                    "storing data",
                                    self.structure.name,
                                    inspect.stack()[0][3],
                                    type_,
                                    value_,
                                    traceback_,
                                    message,
                                    self._simulation_data.debug,
                                )

    def _check_line_size(self, data_line, min_line_size):
        if 0 < len(data_line) < min_line_size:
            message = (
                "Data line {} only has {} entries, "
                "minimum number of entries is "
                "{}.".format(data_line, len(data_line), min_line_size)
            )
            type_, value_, traceback_ = sys.exc_info()
            raise MFDataException(
                self.structure.get_model(),
                self.structure.get_package(),
                self.structure.path,
                "storing data",
                self.structure.name,
                inspect.stack()[0][3],
                type_,
                value_,
                traceback_,
                message,
                self._simulation_data.debug,
            )

    def set_data(self, data, autofill=False):
        self._set_data(data, autofill)

    def append_data(self, data):
        try:
            self._resync()
            if self._get_storage_obj() is None:
                self._data_storage = self._new_storage()
            # store data
            self._get_storage_obj().append_data(data)
        except Exception as ex:
            type_, value_, traceback_ = sys.exc_info()
            raise MFDataException(
                self.structure.get_model(),
                self.structure.get_package(),
                self._path,
                "appending data",
                self.structure.name,
                inspect.stack()[0][3],
                type_,
                value_,
                traceback_,
                None,
                self._simulation_data.debug,
                ex,
            )

    def append_list_as_record(self, record):
        self._resync()
        try:
            # convert to tuple
            tuple_record = ()
            for item in record:
                tuple_record += (item,)
            # store
            self._get_storage_obj().append_data([tuple_record])
        except Exception as ex:
            type_, value_, traceback_ = sys.exc_info()
            raise MFDataException(
                self.structure.get_model(),
                self.structure.get_package(),
                self._path,
                "appending data",
                self.structure.name,
                inspect.stack()[0][3],
                type_,
                value_,
                traceback_,
                None,
                self._simulation_data.debug,
                ex,
            )

    def update_record(self, record, key_index):
        self.append_list_as_record(record)

    def search_data(self, search_term, col=None):
        try:
            data = self._get_storage_obj().get_data()
            if data is not None:
                search_term = search_term.lower()
                for row in data:
                    col_num = 0
                    for val in row:
                        if (
                            val is not None
                            and val.lower() == search_term
                            and (col == None or col == col_num)
                        ):
                            return (row, col)
                        col_num += 1
            return None
        except Exception as ex:
            type_, value_, traceback_ = sys.exc_info()
            if col is None:
                col = ""
            raise MFDataException(
                self.structure.get_model(),
                self.structure.get_package(),
                self._path,
                "searching for data",
                self.structure.name,
                inspect.stack()[0][3],
                type_,
                value_,
                traceback_,
                "search_term={}\ncol={}".format(search_term, col),
                self._simulation_data.debug,
                ex,
            )

    def get_file_entry(
        self,
        values_only=False,
        ext_file_action=ExtFileAction.copy_relative_paths,
    ):
        return self._get_file_entry(values_only, ext_file_action)

    def _get_file_entry(
        self,
        values_only=False,
        ext_file_action=ExtFileAction.copy_relative_paths,
    ):
        try:
            # freeze model grid to boost performance
            self._data_dimensions.lock()
            # init
            indent = self._simulation_data.indent_string
            file_entry = []
            storage = self._get_storage_obj()
            if storage is None or not storage.has_data():
                return ""

            # write out initial comments
            if storage.pre_data_comments:
                file_entry.append(storage.pre_data_comments.get_file_entry())
        except Exception as ex:
            type_, value_, traceback_ = sys.exc_info()
            raise MFDataException(
                self.structure.get_model(),
                self.structure.get_package(),
                self._path,
                "get file entry initialization",
                self.structure.name,
                inspect.stack()[0][3],
                type_,
                value_,
                traceback_,
                None,
                self._simulation_data.debug,
                ex,
            )

        if (
            storage.layer_storage.first_item().data_storage_type
            == DataStorageType.external_file
        ):
            try:
                ext_string = self._get_external_formatting_string(
                    0, ext_file_action
                )
                file_entry.append("{}{}{}".format(indent, indent, ext_string))
                # write file

            except Exception as ex:
                type_, value_, traceback_ = sys.exc_info()
                raise MFDataException(
                    self.structure.get_model(),
                    self.structure.get_package(),
                    self._path,
                    "formatting external file string",
                    self.structure.name,
                    inspect.stack()[0][3],
                    type_,
                    value_,
                    traceback_,
                    None,
                    self._simulation_data.debug,
                    ex,
                )
        else:
            try:
                data_complete = storage.get_data()
                if (
                    storage.layer_storage.first_item().data_storage_type
                    == DataStorageType.internal_constant
                ):
                    data_lines = 1
                else:
                    data_lines = len(data_complete)
            except Exception as ex:
                type_, value_, traceback_ = sys.exc_info()
                raise MFDataException(
                    self.structure.get_model(),
                    self.structure.get_package(),
                    self._path,
                    "getting data from storage",
                    self.structure.name,
                    inspect.stack()[0][3],
                    type_,
                    value_,
                    traceback_,
                    None,
                    self._simulation_data.debug,
                    ex,
                )

            # loop through list line by line - assumes first data_item size
            # is representative
            self._crnt_line_num = 1
            for mflist_line in range(0, data_lines):
                text_line = []
                index = 0
                self._get_file_entry_record(
                    data_complete,
                    mflist_line,
                    text_line,
                    index,
                    self.structure,
                    storage,
                    indent,
                )

                # include comments
                if (
                    mflist_line in storage.comments
                    and storage.comments[mflist_line].text
                ):
                    text_line.append(storage.comments[mflist_line].text)

                file_entry.append(
                    "{}{}\n".format(indent, indent.join(text_line))
                )
                self._crnt_line_num += 1

        # unfreeze model grid
        self._data_dimensions.unlock()
        return "".join(file_entry)

    def _get_file_entry_record(
        self,
        data_complete,
        mflist_line,
        text_line,
        index,
        data_set,
        storage,
        indent,
    ):
        if (
            storage.layer_storage.first_item().data_storage_type
            == DataStorageType.internal_constant
        ):
            try:
                #  constant data
                data_type = self.structure.data_item_structures[1].type
                const_str = self._get_constant_formatting_string(
                    storage.get_const_val(0), 0, data_type, ""
                )
                text_line.append(
                    "{}{}{}".format(indent, indent, const_str.upper())
                )
            except Exception as ex:
                type_, value_, traceback_ = sys.exc_info()
                raise MFDataException(
                    self.structure.get_model(),
                    self.structure.get_package(),
                    self._path,
                    "getting constant data",
                    self.structure.name,
                    inspect.stack()[0][3],
                    type_,
                    value_,
                    traceback_,
                    None,
                    self._simulation_data.debug,
                    ex,
                )
        else:
            data_dim = self._data_dimensions
            data_line = data_complete[mflist_line]
            for data_item in data_set.data_item_structures:
                if data_item.is_aux:
                    try:
                        aux_var_names = (
                            data_dim.package_dim.get_aux_variables()
                        )
                        if aux_var_names is not None:
                            for aux_var_name in aux_var_names[0]:
                                if aux_var_name.lower() != "auxiliary":
                                    data_val = data_line[index]
                                    text_line.append(
                                        to_string(
                                            data_val,
                                            data_item.type,
                                            self._simulation_data,
                                            self._data_dimensions,
                                            data_item.is_cellid,
                                            data_item.possible_cellid,
                                            data_item,
                                        )
                                    )
                                    index += 1
                    except Exception as ex:
                        type_, value_, traceback_ = sys.exc_info()
                        raise MFDataException(
                            self.structure.get_model(),
                            self.structure.get_package(),
                            self._path,
                            "processing auxiliary " "variables",
                            self.structure.name,
                            inspect.stack()[0][3],
                            type_,
                            value_,
                            traceback_,
                            None,
                            self._simulation_data.debug,
                            ex,
                        )
                elif data_item.type == DatumType.record:
                    # record within a record, recurse
                    self._get_file_entry_record(
                        data_complete,
                        mflist_line,
                        text_line,
                        index,
                        data_item,
                        storage,
                        indent,
                    )
                elif (
                    not data_item.is_boundname
                    or data_dim.package_dim.boundnames()
                ) and (
                    not data_item.optional
                    or data_item.name_length < 5
                    or not data_item.is_mname
                    or not storage.in_model
                ):
                    data_complete_len = len(data_line)
                    if data_complete_len <= index:
                        if data_item.optional == False:
                            message = (
                                "Not enough data provided "
                                "for {}. Data for required data "
                                'item "{}" not '
                                "found (data path: {})"
                                ".".format(
                                    self.structure.name,
                                    data_item.name,
                                    self._path,
                                )
                            )
                            type_, value_, traceback_ = sys.exc_info()
                            raise MFDataException(
                                self.structure.get_model(),
                                self.structure.get_package(),
                                self._path,
                                "building file entry record",
                                self.structure.name,
                                inspect.stack()[0][3],
                                type_,
                                value_,
                                traceback_,
                                message,
                                self._simulation_data.debug,
                            )
                        else:
                            break
                    try:
                        # resolve size of data
                        resolved_shape, shape_rule = data_dim.get_data_shape(
                            data_item,
                            self.structure,
                            [data_line],
                            repeating_key=self._current_key,
                        )
                        data_val = data_line[index]
                        if data_item.is_cellid or (
                            data_item.possible_cellid
                            and storage._validate_cellid([data_val], 0)
                        ):
                            if (
                                data_item.shape is not None
                                and len(data_item.shape) > 0
                                and data_item.shape[0] == "ncelldim"
                            ):
                                model_grid = data_dim.get_model_grid()
                                cellid_size = (
                                    model_grid.get_num_spatial_coordinates()
                                )
                                data_item.remove_cellid(
                                    resolved_shape, cellid_size
                                )
                        data_size = 1
                        if len(
                            resolved_shape
                        ) == 1 and datautil.DatumUtil.is_int(
                            resolved_shape[0]
                        ):
                            data_size = int(resolved_shape[0])
                            if data_size < 0:
                                # unable to resolve data size based on shape, use
                                # the data heading names to resolve data size
                                data_size = storage.resolve_data_size(index)
                    except Exception as ex:
                        type_, value_, traceback_ = sys.exc_info()
                        raise MFDataException(
                            self.structure.get_model(),
                            self.structure.get_package(),
                            self._path,
                            "resolving data shape",
                            self.structure.name,
                            inspect.stack()[0][3],
                            type_,
                            value_,
                            traceback_,
                            "Verify that your data is the " "correct shape",
                            self._simulation_data.debug,
                            ex,
                        )
                    for data_index in range(0, data_size):
                        if data_complete_len > index:
                            data_val = data_line[index]
                            if data_item.type == DatumType.keyword:
                                if data_val is not None:
                                    text_line.append(data_item.display_name)
                                if self.structure.block_variable:
                                    # block variables behave differently for
                                    # now.  this needs to be resolved
                                    # more consistently at some point
                                    index += 1
                            elif data_item.type == DatumType.keystring:
                                if data_val is not None:
                                    text_line.append(data_val)
                                index += 1

                                # keystring must be at the end of the line so
                                # everything else is part of the keystring data
                                data_key = data_val.lower()
                                if data_key not in data_item.keystring_dict:
                                    keystr_struct = data_item.keystring_dict[
                                        "{}record".format(data_key)
                                    ]
                                else:
                                    keystr_struct = data_item.keystring_dict[
                                        data_key
                                    ]
                                if isinstance(
                                    keystr_struct, mfstructure.MFDataStructure
                                ):
                                    # data items following keystring
                                    ks_structs = (
                                        keystr_struct.data_item_structures[1:]
                                    )
                                else:
                                    # key string stands alone
                                    ks_structs = [keystr_struct]
                                ks_struct_index = 0
                                max_index = len(ks_structs) - 1
                                for data_index in range(
                                    index, data_complete_len
                                ):
                                    if data_line[data_index] is not None:
                                        try:
                                            k_data_item = ks_structs[
                                                ks_struct_index
                                            ]
                                            text_line.append(
                                                to_string(
                                                    data_line[data_index],
                                                    k_data_item.type,
                                                    self._simulation_data,
                                                    self._data_dimensions,
                                                    k_data_item.is_cellid,
                                                    k_data_item.possible_cellid,
                                                    k_data_item,
                                                )
                                            )
                                        except Exception as ex:
                                            message = (
                                                "An error occurred "
                                                "while converting data "
                                                "to a string. This "
                                                "error occurred while "
                                                'processing "{}" line '
                                                '{} data item "{}".'
                                                "(data path: {})"
                                                ".".format(
                                                    self.structure.name,
                                                    data_item.name,
                                                    self._crnt_line_num,
                                                    self._path,
                                                )
                                            )
                                            (
                                                type_,
                                                value_,
                                                traceback_,
                                            ) = sys.exc_info()
                                            raise MFDataException(
                                                self.structure.get_model(),
                                                self.structure.get_package(),
                                                self._path,
                                                "converting data "
                                                "to a string",
                                                self.structure.name,
                                                inspect.stack()[0][3],
                                                type_,
                                                value_,
                                                traceback_,
                                                message,
                                                self._simulation_data.debug,
                                                ex,
                                            )
                                        if ks_struct_index < max_index:
                                            # increment until last record
                                            # entry then repeat last entry
                                            ks_struct_index += 1
                                index = data_index
                            elif data_val is not None and (
                                not isinstance(data_val, float)
                                or not math.isnan(data_val)
                            ):
                                try:
                                    if data_item.tagged and data_index == 0:
                                        # data item tagged, include data item name
                                        # as a keyword
                                        text_line.append(
                                            to_string(
                                                data_val,
                                                DatumType.string,
                                                self._simulation_data,
                                                self._data_dimensions,
                                                False,
                                                data_item=data_item,
                                            )
                                        )
                                        index += 1
                                        data_val = data_line[index]
                                    text_line.append(
                                        to_string(
                                            data_val,
                                            data_item.type,
                                            self._simulation_data,
                                            self._data_dimensions,
                                            data_item.is_cellid,
                                            data_item.possible_cellid,
                                            data_item,
                                        )
                                    )
                                except Exception as ex:
                                    message = (
                                        "An error occurred while "
                                        "converting data to a "
                                        "string. "
                                        "This error occurred while "
                                        'processing "{}" line {} data '
                                        'item "{}".(data path: {})'
                                        ".".format(
                                            self.structure.name,
                                            data_item.name,
                                            self._crnt_line_num,
                                            self._path,
                                        )
                                    )
                                    type_, value_, traceback_ = sys.exc_info()
                                    raise MFDataException(
                                        self.structure.get_model(),
                                        self.structure.get_package(),
                                        self._path,
                                        "converting data " "to a string",
                                        self.structure.name,
                                        inspect.stack()[0][3],
                                        type_,
                                        value_,
                                        traceback_,
                                        message,
                                        self._simulation_data.debug,
                                        ex,
                                    )
                                index += 1
                        elif not data_item.optional and shape_rule is None:
                            message = (
                                "Not enough data provided "
                                "for {}. Data for required data "
                                'item "{}" not '
                                "found (data path: {})"
                                ".".format(
                                    self.structure.name,
                                    data_item.name,
                                    self._path,
                                )
                            )
                            type_, value_, traceback_ = sys.exc_info()
                            raise MFDataException(
                                self.structure.get_model(),
                                self.structure.get_package(),
                                self._path,
                                "building data line",
                                self.structure.name,
                                inspect.stack()[0][3],
                                type_,
                                value_,
                                traceback_,
                                message,
                                self._simulation_data.debug,
                            )

    def load(
        self,
        first_line,
        file_handle,
        block_header,
        pre_data_comments=None,
        external_file_info=None,
    ):
        super(MFList, self).load(
            first_line, file_handle, block_header, pre_data_comments=None
        )
        self._resync()
        file_access = MFFileAccessList(
            self.structure,
            self._data_dimensions,
            self._simulation_data,
            self._path,
            self._current_key,
        )
        storage = self._get_storage_obj()
        result = file_access.load_from_package(
            first_line, file_handle, storage, pre_data_comments
        )
        if external_file_info is not None:
            storage.point_to_existing_external_file(external_file_info, 0)
        return result

    def _new_storage(self, stress_period=0):
        return DataStorage(
            self._simulation_data,
            self._model_or_sim,
            self._data_dimensions,
            self._get_file_entry,
            DataStorageType.internal_array,
            DataStructureType.recarray,
            stress_period=stress_period,
            data_path=self._path,
        )

    def _get_storage_obj(self):
        return self._data_storage

    def plot(
        self,
        key=None,
        names=None,
        filename_base=None,
        file_extension=None,
        mflay=None,
        **kwargs
    ):
        """
        Plot boundary condition (MfList) data

        Parameters
        ----------
        key : str
            MfList dictionary key. (default is None)
        names : list
            List of names for figure titles. (default is None)
        filename_base : str
            Base file name that will be used to automatically generate file
            names for output image files. Plots will be exported as image
            files if file_name_base is not None. (default is None)
        file_extension : str
            Valid matplotlib.pyplot file extension for savefig(). Only used
            if filename_base is not None. (default is 'png')
        mflay : int
            MODFLOW zero-based layer number to return.  If None, then all
            all layers will be included. (default is None)
        **kwargs : dict
            axes : list of matplotlib.pyplot.axis
                List of matplotlib.pyplot.axis that will be used to plot
                data for each layer. If axes=None axes will be generated.
                (default is None)
            pcolor : bool
                Boolean used to determine if matplotlib.pyplot.pcolormesh
                plot will be plotted. (default is True)
            colorbar : bool
                Boolean used to determine if a color bar will be added to
                the matplotlib.pyplot.pcolormesh. Only used if pcolor=True.
                (default is False)
            inactive : bool
                Boolean used to determine if a black overlay in inactive
                cells in a layer will be displayed. (default is True)
            contour : bool
                Boolean used to determine if matplotlib.pyplot.contour
                plot will be plotted. (default is False)
            clabel : bool
                Boolean used to determine if matplotlib.pyplot.clabel
                will be plotted. Only used if contour=True. (default is False)
            grid : bool
                Boolean used to determine if the model grid will be plotted
                on the figure. (default is False)
            masked_values : list
                List of unique values to be excluded from the plot.

        Returns
        ----------
        out : list
            Empty list is returned if filename_base is not None. Otherwise
            a list of matplotlib.pyplot.axis is returned.
        """
        from flopy.plot import PlotUtilities

        if not self.plottable:
            raise TypeError("Simulation level packages are not plottable")

        if "cellid" not in self.dtype.names:
            return

        PlotUtilities._plot_mflist_helper(
            mflist=self,
            key=key,
            kper=None,
            names=names,
            filename_base=None,
            file_extension=None,
            mflay=None,
            **kwargs
        )


class MFTransientList(MFList, mfdata.MFTransient, DataListInterface):
    """
    Provides an interface for the user to access and update MODFLOW transient
    list data.

    Parameters
    ----------
    sim_data : MFSimulationData
        data contained in the simulation
    structure : MFDataStructure
        describes the structure of the data
    data : list or ndarray
        actual data
    enable : bool
        enable/disable the array
    path : tuple
        path in the data dictionary to this MFArray
    dimensions : MFDataDimensions
        dimension information related to the model, package, and array

    Methods
    -------
    add_transient_key : (transient_key : int)
        Adds a new transient time allowing data for that time to be stored and
        retrieved using the key "transient_key"
    add_one :(transient_key : int)
        Adds one to the data stored at key "transient_key"
    get_data : (key : int) : ndarray
        Returns the data during time "key".
    set_data : (data : ndarray/list, multiplier : float, key : int)
        Sets the contents of the data at time "key" to "data" with
        multiplier "multiplier".
    load : (first_line : string, file_handle : file descriptor,
            block_header : MFBlockHeader, pre_data_comments : MFComment) :
            tuple (bool, string)
        Loads data from first_line (the first line of data) and open file
        file_handle which is pointing to the second line of data.  Returns a
        tuple with the first item indicating whether all data was read
        and the second item being the last line of text read from the file.
    get_file_entry : (key : int) : string
        Returns a string containing the data at time "key".
    append_list_as_record : (data : list, key : int)
        Appends the list "data" as a single record in this list's recarray at
        time "key".  Assumes "data" has the correct dimensions.
    update_record : (record : list, key_index : int, key : int)
        Updates a record at index "key_index" and time "key" with the contents
        of "record".  If the index does not exist update_record appends the
        contents of "record" to this list's recarray.
    See Also
    --------

    Notes
    -----

    Examples
    --------


    """

    def __init__(
        self,
        sim_data,
        model_or_sim,
        structure,
        enable=True,
        path=None,
        dimensions=None,
        package=None,
    ):
        super(MFTransientList, self).__init__(
            sim_data=sim_data,
            model_or_sim=model_or_sim,
            structure=structure,
            data=None,
            enable=enable,
            path=path,
            dimensions=dimensions,
            package=package,
        )
        self._transient_setup(self._data_storage)
        self.repeating = True

    @property
    def data_type(self):
        return DataType.transientlist

    @property
    def dtype(self):
        data = self.get_data()
        if len(data) > 0:
            return data[0].dtype
        else:
            return None

    @property
    def masked_4D_arrays(self):
        model_grid = self._data_dimensions.get_model_grid()
        nper = self._data_dimensions.package_dim.model_dim[
            0
        ].simulation_time.get_num_stress_periods()
        # get the first kper
        arrays = self.to_array(kper=0, mask=True)

        if arrays is not None:
            # initialize these big arrays
            if model_grid.grid_type() == DiscretizationType.DIS:
                m4ds = {}
                for name, array in arrays.items():
                    m4d = np.zeros(
                        (
                            nper,
                            model_grid.num_layers,
                            model_grid.num_rows,
                            model_grid.num_columns,
                        )
                    )
                    m4d[0, :, :, :] = array
                    m4ds[name] = m4d
                for kper in range(1, nper):
                    arrays = self.to_array(kper=kper, mask=True)
                    for name, array in arrays.items():
                        m4ds[name][kper, :, :, :] = array
                return m4ds
            else:
                m3ds = {}
                for name, array in arrays.items():
                    m3d = np.zeros(
                        (
                            nper,
                            model_grid.num_layers,
                            model_grid.num_cells_per_layer(),
                        )
                    )
                    m3d[0, :, :] = array
                    m3ds[name] = m3d
                for kper in range(1, nper):
                    arrays = self.to_array(kper=kper, mask=True)
                    for name, array in arrays.items():
                        m3ds[name][kper, :, :] = array
                return m3ds

    def masked_4D_arrays_itr(self):
        model_grid = self._data_dimensions.get_model_grid()
        nper = self._data_dimensions.package_dim.model_dim[
            0
        ].simulation_time.get_num_stress_periods()
        # get the first kper
        arrays = self.to_array(kper=0, mask=True)

        if arrays is not None:
            # initialize these big arrays
            for name, array in arrays.items():
                if model_grid.grid_type() == DiscretizationType.DIS:
                    m4d = np.zeros(
                        (
                            nper,
                            model_grid.num_layers(),
                            model_grid.num_rows(),
                            model_grid.num_columns(),
                        )
                    )
                    m4d[0, :, :, :] = array
                    for kper in range(1, nper):
                        arrays = self.to_array(kper=kper, mask=True)
                        for tname, array in arrays.items():
                            if tname == name:
                                m4d[kper, :, :, :] = array
                    yield name, m4d
                else:
                    m3d = np.zeros(
                        (
                            nper,
                            model_grid.num_layers(),
                            model_grid.num_cells_per_layer(),
                        )
                    )
                    m3d[0, :, :] = array
                    for kper in range(1, nper):
                        arrays = self.to_array(kper=kper, mask=True)
                        for tname, array in arrays.items():
                            if tname == name:
                                m3d[kper, :, :] = array
                    yield name, m3d

    def to_array(self, kper=0, mask=False):
        return super(MFTransientList, self).to_array(kper, mask)

    def remove_transient_key(self, transient_key):
        if transient_key in self._data_storage:
            del self._data_storage[transient_key]

    def add_transient_key(self, transient_key):
        super(MFTransientList, self).add_transient_key(transient_key)
        if isinstance(transient_key, int):
            stress_period = transient_key
        else:
            stress_period = 1
        self._data_storage[transient_key] = super(
            MFTransientList, self
        )._new_storage(stress_period)

    @property
    def data(self):
        return self.get_data()

    def store_as_external_file(
        self,
        external_file_path,
        binary=False,
        replace_existing_external=True,
        check_data=True,
    ):
        sim_time = self._data_dimensions.package_dim.model_dim[
            0
        ].simulation_time
        num_sp = sim_time.get_num_stress_periods()
        for sp in range(0, num_sp):
            if sp in self._data_storage:
                self._current_key = sp
                layer_storage = self._get_storage_obj().layer_storage
                if (
                    layer_storage.get_total_size() > 0
                    and self._get_storage_obj()
                    .layer_storage[0]
                    .layer_storage_type
                    != DataStorageType.external_file
                ):
                    fname, ext = os.path.splitext(external_file_path)
                    full_name = "{}_{}{}".format(fname, sp + 1, ext)
                    super(MFTransientList, self).store_as_external_file(
                        full_name,
                        binary,
                        replace_existing_external,
                        check_data,
                    )

    def get_data(self, key=None, apply_mult=False, **kwargs):
        if self._data_storage is not None and len(self._data_storage) > 0:
            if key is None:
                if "array" in kwargs:
                    output = []
                    sim_time = self._data_dimensions.package_dim.model_dim[
                        0
                    ].simulation_time
                    num_sp = sim_time.get_num_stress_periods()
                    for sp in range(0, num_sp):
                        if sp in self._data_storage:
                            self.get_data_prep(sp)
                            output.append(
                                super(MFTransientList, self).get_data(
                                    apply_mult=apply_mult
                                )
                            )
                        else:
                            output.append(None)
                    return output
                else:
                    output = {}
                    for key in self._data_storage.keys():
                        self.get_data_prep(key)
                        output[key] = super(MFTransientList, self).get_data(
                            apply_mult=apply_mult
                        )
                    return output
            self.get_data_prep(key)
            return super(MFTransientList, self).get_data(apply_mult=apply_mult)
        else:
            return None

    def set_data(self, data, key=None, autofill=False):
        if isinstance(data, dict) or isinstance(data, OrderedDict):
            if "filename" not in data:
                # each item in the dictionary is a list for one stress period
                # the dictionary key is the stress period the list is for
                del_keys = []
                for key, list_item in data.items():
                    if list_item is None:
                        self.remove_transient_key(key)
                        del_keys.append(key)
                    else:
                        self._set_data_prep(list_item, key)
                        super(MFTransientList, self).set_data(
                            list_item, autofill=autofill
                        )
                for key in del_keys:
                    del data[key]
            else:
                self._set_data_prep(data["data"], key)
                super(MFTransientList, self).set_data(data, autofill)
        else:
            if key is None:
                # search for a key
                new_key_index = self.structure.first_non_keyword_index()
                if new_key_index is not None and len(data) > new_key_index:
                    key = data[new_key_index]
                else:
                    key = 0
            if data is None:
                self.remove_transient_key(key)
            else:
                self._set_data_prep(data, key)
                super(MFTransientList, self).set_data(data, autofill)

    def get_file_entry(
        self, key=0, ext_file_action=ExtFileAction.copy_relative_paths
    ):
        self._get_file_entry_prep(key)
        return super(MFTransientList, self).get_file_entry(
            ext_file_action=ext_file_action
        )

    def load(
        self,
        first_line,
        file_handle,
        block_header,
        pre_data_comments=None,
        external_file_info=None,
    ):
        self._load_prep(block_header)
        return super(MFTransientList, self).load(
            first_line,
            file_handle,
            block_header,
            pre_data_comments,
            external_file_info,
        )

    def append_list_as_record(self, record, key=0):
        self._append_list_as_record_prep(record, key)
        super(MFTransientList, self).append_list_as_record(record)

    def update_record(self, record, key_index, key=0):
        self._update_record_prep(key)
        super(MFTransientList, self).update_record(record, key_index)

    def _new_storage(self, stress_period=0):
        return OrderedDict()

    def _get_storage_obj(self):
        if (
            self._current_key is None
            or self._current_key not in self._data_storage
        ):
            return None
        return self._data_storage[self._current_key]

    def plot(
        self,
        key=None,
        names=None,
        kper=0,
        filename_base=None,
        file_extension=None,
        mflay=None,
        **kwargs
    ):
        """
        Plot stress period boundary condition (MfList) data for a specified
        stress period

        Parameters
        ----------
        key : str
            MfList dictionary key. (default is None)
        names : list
            List of names for figure titles. (default is None)
        kper : int
            MODFLOW zero-based stress period number to return. (default is zero)
        filename_base : str
            Base file name that will be used to automatically generate file
            names for output image files. Plots will be exported as image
            files if file_name_base is not None. (default is None)
        file_extension : str
            Valid matplotlib.pyplot file extension for savefig(). Only used
            if filename_base is not None. (default is 'png')
        mflay : int
            MODFLOW zero-based layer number to return.  If None, then all
            all layers will be included. (default is None)
        **kwargs : dict
            axes : list of matplotlib.pyplot.axis
                List of matplotlib.pyplot.axis that will be used to plot
                data for each layer. If axes=None axes will be generated.
                (default is None)
            pcolor : bool
                Boolean used to determine if matplotlib.pyplot.pcolormesh
                plot will be plotted. (default is True)
            colorbar : bool
                Boolean used to determine if a color bar will be added to
                the matplotlib.pyplot.pcolormesh. Only used if pcolor=True.
                (default is False)
            inactive : bool
                Boolean used to determine if a black overlay in inactive
                cells in a layer will be displayed. (default is True)
            contour : bool
                Boolean used to determine if matplotlib.pyplot.contour
                plot will be plotted. (default is False)
            clabel : bool
                Boolean used to determine if matplotlib.pyplot.clabel
                will be plotted. Only used if contour=True. (default is False)
            grid : bool
                Boolean used to determine if the model grid will be plotted
                on the figure. (default is False)
            masked_values : list
                List of unique values to be excluded from the plot.

        Returns
        ----------
        out : list
            Empty list is returned if filename_base is not None. Otherwise
            a list of matplotlib.pyplot.axis is returned.
        """
        from flopy.plot import PlotUtilities

        if not self.plottable:
            raise TypeError("Simulation level packages are not plottable")

        # model.plot() will not work for a mf6 model oc package unless
        # this check is here
        if self.get_data() is None:
            return

        if "cellid" not in self.dtype.names:
            return

        axes = PlotUtilities._plot_mflist_helper(
            self,
            key=key,
            names=names,
            kper=kper,
            filename_base=filename_base,
            file_extension=file_extension,
            mflay=mflay,
            **kwargs
        )
        return axes


class MFMultipleList(MFTransientList):
    """
    Provides an interface for the user to access and update MODFLOW multiple
    list data.  This is list data that is in the same format as the
    MFTransientList, but is not time based.

    Parameters
    ----------
    sim_data : MFSimulationData
        data contained in the simulation
    structure : MFDataStructure
        describes the structure of the data
    data : list or ndarray
        actual data
    enable : bool
        enable/disable the array
    path : tuple
        path in the data dictionary to this MFArray
    dimensions : MFDataDimensions
        dimension information related to the model, package, and array

    See Also
    --------

    Notes
    -----

    Examples
    --------


    """

    def __init__(
        self,
        sim_data,
        model_or_sim,
        structure,
        enable=True,
        path=None,
        dimensions=None,
        package=None,
    ):
        super(MFMultipleList, self).__init__(
            sim_data=sim_data,
            model_or_sim=model_or_sim,
            structure=structure,
            enable=enable,
            path=path,
            dimensions=dimensions,
            package=package,
        )

    def get_data(self, key=None, apply_mult=False, **kwargs):
        return super(MFMultipleList, self).get_data(
            key=key, apply_mult=apply_mult, **kwargs
        )
