import numpy as np
from numba import jit
import os
from scipy.signal import decimate
import tables as tb
import shutil
from . import meta_handlers
import logging
from datetime import datetime
from uuid import uuid4
from glob import glob
from .open_ephys_helpers import loadContinuous
from tqdm import tqdm
import pickle
try:
    import matplotlib.pyplot as plt
except RuntimeError:
    pass

LOG_FORMATTER = logging.Formatter("%(asctime)s %(levelname)-7.7s:  %(message)s")
LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setFormatter(LOG_FORMATTER)
LOGGER.addHandler(console_handler)



EXPECTED_LFP_ROWS = 1800000  # number lfp samples per channel for openphys LFP file (30 min @ 1kHz)
STREAM_PLOT_NSAMP = 100000
EVENT_PLOT_NSAMP = 1000000

def process_spikegl_recording(raw_fn_list: list,
                              meta_fn_list: list,
                              save_prefix: str,
                              neural_channel_numbers: (list, range),
                              meta_stream_dict:dict,
                              meta_event_dict: dict,
                              voyeur_paths: list,
                              pl_trig_ch: int,
                              debug_plots=True,
                              file_dtype='int16',
                              clean_on_excemption=True):
    """
    Creates .dat file from multiple recording bins from spikegl.
    Preforms powerline trigger removal and makes a downsampled LFP file.

    :param raw_fns: list of raw filenames (bin)
    :param meta_fns: list of meta filenames (meta)
    :param save_prefix: prefix with which to name the resulting DAT file
    :param neural_channel_numbers: list (or range) of channels that should be considered neural. These will
    be saved to LFP file, and will be PL filtered
    :param meta_stream_dict: Dictionary of streams to save - {'streamname': ch#}
    :param meta_event_dict: Dictionary of events and channels to process. Event names must have associeated
    processor in the meta module.
    :param voyeur_paths: list of paths to voyeur run files containing behavioral data.
    :param pl_trig_ch: CHANNEL number where power line trigger is recorded. Index will be calculated.
    :param file_dtype: string specifying how is the data saved (default is int16). This can be read from metafile.
    :return:
    """
    file_dtype = np.dtype(file_dtype)
    log_fn = "{}_{}.log".format(save_prefix, datetime.now().strftime('%Y%m%dh%Hm%ms%S'))
    log_file_handler = logging.FileHandler(log_fn)
    log_file_handler.setFormatter(LOG_FORMATTER)
    LOGGER.addHandler(log_file_handler)

    if type(raw_fn_list) == str:
        raw_fns = (raw_fn_list,)
    else:
        raw_fns = raw_fn_list
    if type(meta_fn_list) == str:
        meta_fns = (meta_fn_list,)
    else:
        meta_fns = meta_fn_list
    if type(voyeur_paths) == str:
        voyeur_fns = (voyeur_paths,)
    else:
        voyeur_fns = voyeur_paths

    assert len(raw_fns) == len(meta_fns)
    if len(voyeur_fns) < len(raw_fns):
        logging.warning('{} raw files specified and only '
                        '{} voyeur files.'.format(len(raw_fns),len(voyeur_fns)))
    elif len(voyeur_fns) > len(raw_fns):
        logging.warning('too many voyeur filenames supplied!')

    for fs in (voyeur_fns, raw_fns, meta_fns):
        for f in fs:
            if not os.path.exists(f):
                raise FileNotFoundError('{} not found.'.format(f))

    tmpdirname = 'tmp-{}'.format(uuid4())
    dat_fn = "{}.dat".format(save_prefix)
    temp_dat_fn = os.path.join(tmpdirname, dat_fn)
    lfp_fn = '{}_lfp.h5'.format(save_prefix)
    temp_lfp_fn = os.path.join(tmpdirname, '{}_lfp.h5'.format(save_prefix))
    temp_meta_fn = os.path.join(tmpdirname, '{}_meta.h5'.format(save_prefix))
    meta_fn = '{}_meta.h5'.format(save_prefix)
    separated_prefixes = []

    for fnn in (dat_fn, lfp_fn, meta_fn):
        if os.path.exists(fnn):
            logging.error('Dat already exists: {}. Exiting'.format(dat_fn))
            return

    total_size = 0

    # read all the .meta text files generated by spikegl and extract
    nchs = []  # we're going to collect the numchannels specified for all of the meta files specified to make sure they are the same.
    meta_dtypes = []
    for r, m in zip(raw_fns, meta_fns):
        total_size += os.path.getsize(r)
        chs, fs, meta_dtype = _read_meta(m)
        meta_dtypes.append(meta_dtype)
        nchs.append(len(chs))

    assert len(set(nchs)) == 1  # check that the channel lists are the same for all the runs
    total_chs = nchs[0]
    samps_per_ch = total_size / total_chs / file_dtype.itemsize

    assert len(set(meta_dtypes)) == 1  # check that the datatypes are the same for all the runs
    if meta_dtypes[0] is not None:
        file_dtype = meta_dtypes[0]

    try:
        os.mkdir(tmpdirname)
        with open(temp_dat_fn, 'wb') as f:
            pass

        for i in range(len(raw_fns)):
            separated_prefix = os.path.join(tmpdirname, '{}_{}'.format(save_prefix, i))
            separated_prefixes.append(separated_prefix)
            r_fn = raw_fns[i]
            m_fn = meta_fns[i]
            if not r_fn[0] == m_fn[0]:
                logging.warning('raw and meta filenames are not similar: raw: {}, meta: {}'.format(r_fn, m_fn))
            logging.info('Separating {} to channels...'.format(r_fn))
            chs, fs, _ = _read_meta(m_fn)
            for ch in neural_channel_numbers:
                if ch not in chs:
                    raise ValueError('Neural channel {} is not found in sgl meta file.'.format(ch))
            for m_chs in (meta_event_dict, meta_stream_dict):
                for k, v in m_chs.items():
                    if v not in chs:
                        raise ValueError('Channel {} specified as a meta channel "{}", but not found '
                                         'in recording.'.format(v, k))
            _separate_channels(r_fn, chs, separated_prefix, dtype=file_dtype)

            if pl_trig_ch:
                logging.info('Running PL removal using ch {}...'.format(pl_trig_ch))
                pl_sig = np.fromfile(_gen_channel_fn(separated_prefix, pl_trig_ch), file_dtype)
                if debug_plots and i < 1:
                    plt.plot(pl_sig[:10000])
                    plt.plot(plt.xlim(), [pl_sig.mean()]*2, '--k')
                    plt.title('PL trig debug plot')
                    plt.show()
                pl_trig_times = _find_pl_times(pl_sig)

                # if there's greater that 20% error in the number of PL trigs compared to what we expect,
                # raise exception.
                seconds_recorded = samps_per_ch / fs
                expected_pl_trigs = seconds_recorded * 60.  # this is for 60 Hz (US) powerline AC.
                if np.abs(len(pl_trig_times) - expected_pl_trigs) > expected_pl_trigs * 0.2:
                    raise ValueError("PL trigs")

            else:
                print('No PL trigger channel specified, skipping removal step.')

            logging.info('Writing to {}...'.format(temp_dat_fn))
            with open(temp_dat_fn, 'ab') as f:
                _merge_channels(separated_prefix, chs, f, dtype=file_dtype)
                logging.info('Write complete.')

            if i < 1:
                create_lfp_file = True
            else:
                create_lfp_file = False
            _make_lfp(separated_prefix, neural_channel_numbers, temp_lfp_fn, fs, create_lfp_file, dtype=file_dtype,
                      expectedrows=samps_per_ch)

        make_meta(separated_prefixes, meta_stream_dict, meta_event_dict, voyeur_fns, temp_meta_fn, fs,
                  file_dtype, debug_plots)
        logging.info('meta completed.')

        assert total_size == os.path.getsize(temp_dat_fn)

        logging.info('Renaming temp files...')
        os.rename(temp_dat_fn, dat_fn)
        os.rename(temp_lfp_fn, lfp_fn)
        os.rename(temp_meta_fn, meta_fn)
    except Exception as e:
        logging.exception("message")
    finally:
        if clean_on_excemption and os.path.exists(tmpdirname):
            logging.info('Cleaning up temp files...')
            shutil.rmtree(tmpdirname)
        elif os.path.exists(tmpdirname):
            logging.info('clean_on_exception=False, leaving temp directory.')
        LOGGER.removeHandler(log_file_handler)
        log_file_handler.close()

    return


def process_oEphys_rec(raw_folders,
                       save_prefix,
                       neural_channel_numbers: (list, range),
                       meta_stream_dict: dict,
                       meta_event_dict: dict,
                       voyeur_paths: list,
                       pl_trig_ch: int,
                       raw_prefixes=['100'],
                       debug_plots=True,
                       file_dtype='int16',
                       clean_on_exemption=True):
    """

    :param raw_folders: folder where raw files exist in open ephys format
    :param raw_prefixes: prefix of files
    :param save_prefix:
    :param neural_channel_numbers: openephys channels start at 1.
    :param meta_stream_dict:
    :param meta_event_dict:
    :param voyeur_paths:
    :param pl_trig_ch:
    :param debug_plots:
    :param file_dtype:
    :param clean_on_exemption:
    :return:
    """
    file_dtype = np.dtype(file_dtype)
    completed = False
    log_fn = "{}_{}.log".format(save_prefix, datetime.now().isoformat())
    log_file_handler = logging.FileHandler(log_fn)
    log_file_handler.setFormatter(LOG_FORMATTER)
    LOGGER.addHandler(log_file_handler)

    if type(raw_folders) == str:
        raw_folders = (raw_folders,)
    else:
        raw_folders = raw_folders

    if type(voyeur_paths) == str:
        voyeur_fns = (voyeur_paths,)
    else:
        voyeur_fns = voyeur_paths

    if len(voyeur_fns) < len(raw_folders):
        logging.warning('{} raw files specified and only '
                        '{} voyeur files.'.format(len(raw_folders),len(voyeur_fns)))

    for files in (voyeur_fns, raw_folders):
        for f in files:
            if not os.path.exists(f):
                raise FileNotFoundError('{} not found.'.format(f))

    tmpdirname = 'tmp-{}'.format(uuid4())
    dat_fn = "{}.dat".format(save_prefix)
    temp_dat_fn = os.path.join(tmpdirname, dat_fn)
    lfp_fn = '{}_lfp.h5'.format(save_prefix)
    temp_lfp_fn = os.path.join(tmpdirname, '{}_lfp.h5'.format(save_prefix))
    temp_meta_fn = os.path.join(tmpdirname, '{}_meta.h5'.format(save_prefix))
    meta_fn = '{}_meta.h5'.format(save_prefix)
    separated_prefixes = []
    adc_prefixes = []  # ADC prefixes are needed because ADC channel start at 1 - same as neural...

    for fnn in (dat_fn, lfp_fn):
        if os.path.exists(fnn):
            logging.error('Dat already exists: {}. Exiting'.format(dat_fn))
            return

    bytes_per_sample = file_dtype.itemsize
    n_samples_by_ch = []  # list of the number of samples read into each neural channel.
    total_expected_dat_size = 0 # running value of the expected dat size after every run is appended.

    try:
        os.mkdir(tmpdirname)
        with open(temp_dat_fn, 'wb') as f:
            pass

        for i_run in range(len(raw_folders)):  # iterating through the folders and combine the runs
            fs = 0
            separated_prefix = os.path.join(tmpdirname, '{}_{}'.format(save_prefix, i_run))
            separated_prefixes.append(separated_prefix)
            adc_prefix = separated_prefix + '_ADC'  # for auxillary channels, which start from 1
            adc_prefixes.append(adc_prefix)
            raw_folder = raw_folders[i_run]
            raw_prefix = raw_prefixes[i_run]
            raw_neural_fns = glob(os.path.join(raw_folder, "{}_CH*.continuous".format(raw_prefix)))
            raw_aux_fns = glob(os.path.join(raw_folder, "{}_AUX*.continuous".format(raw_prefix)))
            raw_adc_fns = glob(os.path.join(raw_folder, "{}_ADC*.continuous".format(raw_prefix)))
            raw_neural_chs = [_get_number(p, 'CH') for p in raw_neural_fns]
            raw_aux_chs = [_get_number(p, 'AUX') for p in raw_aux_fns]  # not used currently.
            raw_adc_chs = [_get_number(p, 'ADC') for p in raw_adc_fns]
            for ch in neural_channel_numbers:
                if ch not in raw_neural_chs:
                    raise ValueError('Neural channel {} is not found).'.format(ch))
            for m_chs in (meta_event_dict, meta_stream_dict):
                for k, v in m_chs.items():
                    if v not in raw_adc_chs:
                        raise ValueError('Channel {} specified as a meta channel "{}", but not found '
                                         'in recording.'.format(v, k))

            if pl_trig_ch and pl_trig_ch in raw_adc_chs:
                logging.info('Running PL removal using AUX ch {}...'.format(pl_trig_ch))
                plid = raw_adc_chs.index(pl_trig_ch)
                plfn = raw_adc_fns[plid]
                pl_sig = loadContinuous(plfn, dtype=file_dtype)['data']
                if debug_plots and i_run < 1:
                    plt.plot(pl_sig[:10000])
                    plt.title('PL trig debug plot')
                    plt.show()
                pl_trig_times = _find_pl_times(pl_sig)

                logging.debug('{} pl trig times found'.format(len(pl_trig_times)))
            else:  # we still have to make the files, unlike in the SGL case where they're already created.
                if pl_trig_ch:
                    logging.warning('PL trig channel specified as AUX {}, '
                                    'but this file not found.'.format(pl_trig_ch))
                logging.info('No PL trig removal. Writing temporary dat files...')
            for i_ch in tqdm(neural_channel_numbers, unit='chan', desc='Unpack/PL filter'):
                save_fn = _gen_channel_fn(separated_prefix, i_ch)
                raw_fn = raw_neural_fns[raw_neural_chs.index(i_ch)]
                loaded = loadContinuous(raw_fn, dtype=file_dtype)
                a = loaded['data']
                n_samples_by_ch.append(len(a))
                if not fs:
                    # TODO keep track of number of samples in recording
                    fs = int(loaded['header']['sampleRate'])
                if pl_trig_ch and pl_trig_ch in raw_adc_chs:
                    _rm_pl_i(a, pl_trig_times)
                with open(save_fn, 'wb') as f:
                    a.tofile(f)

            #note: dat file will only have neural channels. Aux channels will not be added to the dat
            total_neural_samples = 0  # sum of samples in all neural channels
            for i_ch in range(len(n_samples_by_ch)):
                total_neural_samples += n_samples_by_ch[i_ch]
                # check if all channels have the same number of samples.
                # could probably be done with filesize...
                if n_samples_by_ch[i_ch] != n_samples_by_ch[0]:
                    raise ValueError('channel {} does not have the same number of channels '
                                     'as channel {}.'.format(neural_channel_numbers[i_ch],
                                                            neural_channel_numbers[0]))
            total_neural_bytes = bytes_per_sample * total_neural_samples
            total_expected_dat_size += total_neural_bytes

            logging.info('Neural channels extracted at {} hz'.format(fs))
            logging.info('Writing to {}...'.format(temp_dat_fn))
            with open(temp_dat_fn, 'ab') as f:
                _merge_channels(separated_prefix, neural_channel_numbers, f, dtype=file_dtype)
                logging.info('Complete.')

            assert os.path.getsize(temp_dat_fn) == total_expected_dat_size

            if i_run < 1:
                create_lfp_file = True
            else:
                create_lfp_file = False
            _make_lfp(separated_prefix, neural_channel_numbers, temp_lfp_fn, fs, create_lfp_file,
                      dtype=file_dtype, expectedrows=EXPECTED_LFP_ROWS)
            # expected rows is hard-coded for a 30 minute recording @ 1kHz

            # todo: handle aux channels too and implement a way to get aux channels into the meta file.
            logging.info('reading ADC channels')

            for ch, raw_fn in zip(tqdm(raw_adc_chs, unit='chan', desc='Unpack ADC chans.'), raw_adc_fns):
                save_fn = _gen_channel_fn(adc_prefix, ch)
                logging.debug('saving ADC ch {} ({}) to "{}"'.format(ch, raw_fn, save_fn))
                loaded = load_continuous(raw_fn, dtype=file_dtype)
                a = loaded['data']
                with open(save_fn, 'wb') as f:
                    a.tofile(f)
        logging.info('Renaming temp dat and lfp files...')
        os.rename(temp_dat_fn, dat_fn)
        os.rename(temp_lfp_fn, lfp_fn)

    except Exception as e:
        logging.error('Failed during neural data handling. No resume is possible.')
        logging.exception("message")
        if os.path.exists(tmpdirname) and clean_on_exemption:
            logging.info('Cleaning up temp files...')
            shutil.rmtree(tmpdirname)
            raise
    finally:  # this will execute regardless of whether or not an exception is caught during try block.
        LOGGER.removeHandler(log_file_handler)
        log_file_handler.close()

    try:
        make_meta(adc_prefixes, meta_stream_dict, meta_event_dict, voyeur_fns, temp_meta_fn, fs,
                  file_dtype, debug_plots)
        logging.info('meta completed.')
        os.rename(temp_meta_fn, meta_fn)
        completed = True
    except Exception as e:
        logging.error('Failed during metadata creation step.')
        logging.exception('message')
        raise
    finally:
        if os.path.exists(tmpdirname) and (completed or clean_on_exemption):
            logging.info('Cleaning up temp files...')
            shutil.rmtree(tmpdirname)
        elif not clean_on_exemption:
            # save some data for an entrypoint back into make_meta.
            d = os.getcwd()
            fp = os.path.join(d, '{}_resume.pickle'.format(dat_fn))
            logging.info('Saving resume pickle file to: {}'.format(fp))
            with open('resume.pickle', 'wb') as f:
                pickle.dump((adc_prefixes, meta_stream_dict, meta_event_dict, voyeur_fns, temp_meta_fn,
                             fs, file_dtype, debug_plots, tmpdirname, save_prefix), f)
        LOGGER.removeHandler(log_file_handler)
        log_file_handler.close()

def resume(resume_file_path):
    """
    Allows resume of metadata creation.
    :param resume_file_path: path to pickle file.
    """

    with open(resume_file_path, 'rb') as f:
        adc_prefixes, meta_stream_dict, meta_event_dict, voyeur_fns, temp_meta_fn, \
        fs, file_dtype, debug_plots, save_prefix = pickle.load(f)
    log_fn = "{}_resume_{}.log".format(save_prefix, datetime.now().isoformat())
    log_file_handler = logging.FileHandler(log_fn)
    log_file_handler.setFormatter(LOG_FORMATTER)
    LOGGER.addHandler(log_file_handler)
    logging.info('Opened {}'.format(resume_file_path))

    make_meta(adc_prefixes, meta_stream_dict, meta_event_dict, voyeur_fns, temp_meta_fn, fs,
              file_dtype, debug_plots)


def _read_meta(path):
    """
    Reads SpikeGL meta file. Returns list of channel numbers in the order they are recorded and the sample rate of the
    acquisition system.[[

    :param path: path to the .meta file
    :return:
    """
    a = open(path)
    b = a.read()
    meta = dict()
    for i in b.splitlines():
        k, vals = i.split('=')
        meta[k] = vals
    channels = []
    chstr = meta['snsSaveChanSubset']
    if chstr == 'all':
        channels = [x for x in range(256)]
    # print (channels)
    else:
        for i in chstr.split(','):
            if ':' in i:
                low, high = [int(x) for x in i.split(':')]
                high += 1
                for ii in range(low, high):
                    channels.append(ii)
            else:
                channels.append(int(i))
    fs = int(meta['niSampRate'])
    logging.debug('n channels: {}'.format(len(channels)))
    logging.debug(str(channels))
    # process the data type if this is specified in the metafile (for Angela)
    dtype = None
    if "dtype" in meta.keys():
        dtstr = meta['dtype']
        logging.info("Data type specified in meta file. Using {}".format(dtstr))
        dtype = np.dtype(dtstr)

    return channels, fs, dtype


def _separate_channels(raw_fn, channels, prefix_str, overwrite=False, append=False,
                       samples_per_read=10**9, dtype=np.dtype('int16')):
    """

    :param raw_fn: name of raw binary file
    :param channels: list or array of channel numbers (for filename purposes)
    :param prefix_str: prefix for saved filenames.
    :param overwrite: should existing files bes
    :param append:
    :param samples_per_read:
    :param dtype: datatype of raw file.
    :return:
    """
    n_ch = len(channels)

    channel_fns = [_gen_channel_fn(prefix_str, x) for x in channels]

    if not overwrite and not append:
        for fn in channel_fns:
            if os.path.exists(fn):
                raise ValueError('Files already exist, set overwrite flag if needed.')

    if not append:
        with open(fn, 'wb') as f:
            pass
    elif append:
        sizes = [os.path.getsize(x) for x in channel_fns]
        assert len(set(sizes)) == 1

    bytes_per_sample = dtype.itemsize

    total_bytes = os.path.getsize(raw_fn)
    samples_per_read -= samples_per_read % n_ch
    bytes_per_read = samples_per_read * bytes_per_sample
    n_steps = int(np.ceil(total_bytes / bytes_per_read))

    assert not total_bytes % (bytes_per_sample * n_ch)

    step_count = 0
    with open(raw_fn, 'rb') as orig_file:
        a = np.fromfile(orig_file, dtype, samples_per_read)
        x = len(a)
        while x:
            step_count += 1
            logging.info('Separating block {} of {}.'.format(step_count, n_steps))
            a.shape = int(a.size / n_ch), n_ch
            for i, fn in enumerate(channel_fns):
                with open(fn, 'ab') as f:
                    a[:, i].tofile(f)
            # loaders next block and repeat if it exists.
            a = np.fromfile(orig_file, dtype, samples_per_read)
            x = len(a)



def _gen_channel_fn(prefix, channel_number):
    """

    :param prefix:
    :param channel_number:
    :return:
    """

    return '{0}_ch{1:04n}.bin'.format(prefix, channel_number)


def _merge_channels(separate_prefix, channels, save_file_obj, samples_per_read=10 ** 9,
                    dtype=np.int16):
    """

    :param separate_prefix:
    :param channels:
    :param save_file_obj:
    :param samples_per_read: number of samples to hold in memory at any given time.
    :param dtype: numpy datatype of binary
    :return:
    """

    fns = [_gen_channel_fn(separate_prefix, x) for x in channels]
    sizes = [os.path.getsize(x) for x in fns]
    assert len(set(sizes)) == 1
    bytes_per = dtype.itemsize

    stepsize_samps = samples_per_read // len(channels)
    stepsize_bytes = stepsize_samps * bytes_per
    n_bytes = sizes[0]
    temp_array = np.zeros((stepsize_samps, len(channels)), dtype=dtype)
    seek = 0

    n_steps = int(np.ceil(n_bytes / stepsize_bytes))
    step_counter = 1

    while seek < n_bytes:  # must do this in blocks because we don't want to loaders all data from all channels.
        logging.info('merging block {} of {}'.format(step_counter, n_steps))
        step_counter += 1
        for i, fn in enumerate(fns):
            with open(fn, 'rb') as f:
                f.seek(seek)
                a = np.fromfile(f, dtype, stepsize_samps)
                temp_array[:len(a), i] = a

        temp_array[:len(a), :].tofile(save_file_obj)
        seek += stepsize_bytes
    return


@jit  # you need this.
def _rm_pl_i(chan_sig, pl_edge_idx):
    """
    removes pl trig signal in place.
    :param chan_sig: signal array (1d)
    :param pl_edge_idx: array of indexes of PL trig.
    :return: none
    """
    n_pl = pl_edge_idx.size
    pl_len = int(np.diff(pl_edge_idx).max())

    sig_len = chan_sig.size

    # handle unsigned integers:
    if 'u' not in chan_sig.dtype.str:
        result_sig = chan_sig - chan_sig.mean(dtype=chan_sig.dtype)
    else:  # convert to int
        dts = chan_sig.dtype.str
        dts2 = dts[0] + "i" + dts[2]  # signed int.
        result_sig = np.zeros(len(chan_sig), dtype=dts2)
        # make sure we don't overflow on conversion to int from uint
        # our conversion will be forced when we subtract int array
        # from the uint array so we want to have some control over how this is done.

        for i in range(len(chan_sig)):
            result_sig[i] = chan_sig[i] - 32767

    sig_pl = np.zeros((n_pl - 1, pl_len), dtype=result_sig.dtype)

    plt.plot(result_sig[:5000])

    for i, edge in enumerate(pl_edge_idx):
        end = edge + pl_len
        if end < sig_len:
            sig_pl[i, :] = result_sig[edge:end]

    pl_template = sig_pl.mean(axis=0).astype(result_sig.dtype)
    pl_template -= pl_template.mean().astype(result_sig.dtype)

    for i in range(n_pl - 1):
        st = pl_edge_idx[i]
        end = pl_edge_idx[i + 1]
        l = end - st
        result_sig[st:end] -= pl_template[:l]
    if 'u' not in chan_sig.dtype.str:
        for i in range(len(chan_sig)):
            chan_sig[i] = result_sig[i]
    else:
        for i in range(len(chan_sig)):  # convert back to uint and offset in the process.
            chan_sig[i] = result_sig[i] + 32767


def _find_pl_times(pl):
    # pl = recording_matrix[:, i_pl]
    pl_threshold = np.mean(pl)
    pl_trig_log = pl > pl_threshold
    pl_edge_detect = np.convolve([1, -1], pl_trig_log, mode='same')
    pl_edge_idx = np.where(pl_edge_detect[1:] == 1)[0]
    return pl_edge_idx


def _make_lfp(raw_files_prefix: str, channels, lfp_filename, acquistion_frequency, create_file=False,
              target_freqency=1000, dtype=np.int16, expectedrows=0):
    """
    Creates a decimated copy of the acquired (or processed) binary file. Only saves specific channels indicated by the
    user. Target frequency is 1kHz, but this can be adjusted as required.

    Output is a .npy file (for now), as this can be easily converted as required.

    :param raw_files_prefix: Path to the binary files (separated by channels).
    :param channels: list of channels to save LFP copies.
    :param save_filename: filename for LFP file to save.
    :param acquistion_frequency: Sampling frequency of the original binary file.
    :param create_file: create lfp file?
    :param target_freqency: Desired sampling frequency of the LFP copy (default is 1 kHz).
    :return:
    """
    logging.info('Making LFP for {}. Loading data...'.format(raw_files_prefix))
    downsample_factor = acquistion_frequency // target_freqency
    lfp_freq = acquistion_frequency / downsample_factor

    if os.path.exists(lfp_filename) and create_file:
        raise ValueError('LFP file already exists.')
    elif create_file:
        with tb.open_file(lfp_filename, 'w') as f:
            n = f.create_group('/', 'lfp')
            n._f_setattr('Frequency_hz', lfp_freq)
            for ch in channels:
                f.create_earray('/lfp/', 'ch_{0:04n}'.format(ch), tb.Int16Atom(), shape=(0,),
                                expectedrows=expectedrows//downsample_factor)
    logging.info("writing LFP to {}".format(lfp_filename))
    with tb.open_file(lfp_filename, 'r+') as f:
        for ch in tqdm(channels, unit='chan', desc='LFP save'):
            fn = _gen_channel_fn(raw_files_prefix, ch)
            a = np.fromfile(fn, dtype=dtype)
            b = decimate(a, downsample_factor, zero_phase=True)
            ch_array = f.get_node('/lfp/ch_{0:04n}'.format(ch))
            ch_array.append(b)
    logging.info('Complete.')


def make_meta(raw_files_prefixes: list, stream_channels, event_channels, voyeur_files, save_fn,
              acquistion_frequency, dtype=np.int16, debug_plots=False):
    """

    :param raw_files_prefixes:
    :param stream_channels:
    :param event_channels:
    :param voyeur_files:
    :param save_fn: name of the meta h5 file to save.
    :param acquistion_frequency:
    :param dtype: dtype the raw files are saved in.
    :param debug_plots:
    :return:
    """
    ch = 1  # in case we have no specified channels we'll read from channel 1...
    logging.info("Making meta HDF5 file {}".format(save_fn))
    with tb.open_file(save_fn, 'w') as f:
        assert isinstance(f, tb.File)
        f.set_node_attr('/', 'acquisition_frequency_hz', acquistion_frequency)
        logging.debug("copying Voyeur beh files:")
        f.create_group('/', 'Voyeur')
        for fn in voyeur_files:
            _, filename = os.path.split(fn)
            v_name, _ = os.path.splitext(filename)
            with tb.open_file(fn, 'r') as run:
                logging.debug("{}".format(fn))
                run.copy_node('/', f.root.Voyeur, v_name, recursive=True)

        for name, ch in stream_channels.items():
            logging.debug('writing stream {}'.format(name))
            stream_chunks = []
            for prefix in raw_files_prefixes:
                fn = _gen_channel_fn(prefix, ch)
                a = np.fromfile(fn, dtype)
                stream_chunks.append(a)
            stream = np.concatenate(stream_chunks)
            if debug_plots:
                plt.plot(stream[:STREAM_PLOT_NSAMP])
                plt.title(name)
                plt.show()
            f.create_carray('/Streams', name, createparents=True, obj=stream)
        f.create_group('/', 'Events')
        for name, ch in event_channels.items():

            stream_chunks = []
            for prefix in raw_files_prefixes:
                fn = _gen_channel_fn(prefix, ch)
                a = np.fromfile(fn, dtype)
                stream_chunks.append(a)
            stream = np.concatenate(stream_chunks)
            if debug_plots:
                plt.plot(stream[:EVENT_PLOT_NSAMP])
                plt.title(name)
                plt.show()
            events = meta_handlers.processors[name](stream, acquistion_frequency)
            f.create_carray('/Events', name, createparents=True, obj=events)

        run_ends = np.zeros(len(raw_files_prefixes), dtype=np.uint64)
        end = 0
        for i, prefix in enumerate(raw_files_prefixes):
            #TODO: if there are no event/stream channels, this will not work.
            sz = os.path.getsize(_gen_channel_fn(prefix, ch))
            end = end + sz
            run_ends[i] = end/dtype.itemsize

        f.create_carray('/Events', 'run_ends', obj=run_ends, title='run end samples.')
    return






def _get_number(path, ch_prefix):

    d, f = os.path.split(path)
    n, e = os.path.splitext(f)
    p, ch = n.split(ch_prefix)
    return int(ch)