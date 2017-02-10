from .base import Session, Unit
from ..loaders import meta_loaders
import tables as tb
import numpy as np
import matplotlib.pyplot as plt

ODOR_FIELD = "olfas:olfa_{}:odor"
ODOR_CONC_FIELD = "odorconc"
TRIAL_NUM_FIELD = "trialNumber"
INHALATION_NODE = "/Events/inhalations_{}"
EXHALATION_NODE = "/Events/exhalations_{}"


class OdorUnit(Unit):
    def __init__(self, unit_id, spiketimes: np.ndarray, rating, session):
        super(OdorUnit, self).__init__(unit_id, spiketimes, rating, session)

    def plot_odor_psth(self, odor, concentration, pre_ms, post_ms, binsize_ms,
                       axis=None, label='', color=None, alpha=1., linewidth=2, linestyle='-',
                       convolve=False):
        """
        Plots odor PSTHs for first inhalations of a specified odorant at a specified concentration.
        Wraps the BaseUnit.plot_psth_times function.

        :param odor: odor name (str)
        :param concentration:
        :param pre_ms: number of milliseconds prior to inhalation to plot
        :param post_ms: number of milliseconds after inhalation to plot
        :param binsize_ms: binsize for histogram
        :param axis: matplotlib axis on which to plot (optional: otherwise, make new axis)
        :param label: string for plot legend
        :param color: matplotlib colorspec for psth line (ie 'k' for black line)
        :param alpha: transparency of psth line (float: 1. is opaque, 0. is transparent)
        :param linewidth: line width for psth plot (float)
        :param linestyle: matplotlib linespec for psth plot
        :param convolve: default is false. If "gaussan" or "boxcar", use these shaped kernels to make plot instead of histogram.
        :return:
        """

        inhs, exhs = self.session.get_first_odor_sniffs(odor, concentration)
        return self.plot_psth_times(inhs, pre_ms, post_ms, binsize_ms, axis=axis, label=label, color=color,
                                    alpha=alpha, linewidth=linewidth, linestyle=linestyle, convolve=convolve)

    def get_odor_rasters(self, odor, concentration, pre_ms, post_ms):
        """

        :param odor:
        :param concentration:
        :param pre_ms:
        :param post_ms:
        :return:
        """
        inhs, exhs = self.session.get_first_odor_sniffs(odor, concentration)
        t_0s_ms = self.session.samples_to_millis(inhs)
        starts = t_0s_ms - pre_ms
        size = int(pre_ms + post_ms)
        return self.get_rasters_ms(starts, size)

    def plot_odor_rasters(self, odor, concentration, pre_ms, post_ms, sniff_overlay=False, axis=None,
                          quick_plot=True, color=None, alpha=1, offset=0, markersize=.5):
        """
        Plots rasters for given odor and concentration condition.

        offset parameter can be used for plotting multiple conditions on the same axis. For instance, if
        condition_1 has 10 trials,call the function for condition_1 with offset of 0, and for condition_2 use
        an offset of 10.

        :param odor: odor name (str)
        :param concentration: odor concentration to plot.
        :param pre_ms: number of milliseconds prior to inhalation to plot
        :param post_ms: number of milliseconds after inhalation to plot
        :param sniff_overlay: sorts rasters by inhalation length and overlays a polygon representing the period of first inhalation.
        :param axis: matplotlib axis on which to plot (optional: otherwise, make new axis)
        :param quick_plot: if False, use a much slower rasterization method that draws lines instead of just using dots.
        :param color: matplotlib colorspec for psth line (ie 'k' for black line)
        :param alpha: transparency of psth line (float: 1. is opaque, 0. is transparent)
        :param offset: used for plotting muliple conditions on same axis.
        :param markersize: size of marker to use for plotting.
        :return: plot axis
        """

        rasters = self.get_odor_rasters(odor, concentration, pre_ms, post_ms)
        x = np.arange(-pre_ms, post_ms)
        if sniff_overlay:
            from matplotlib.patches import Polygon, PathPatch
            inhs, exhs = self.session.get_first_odor_sniffs(odor, concentration)
            n_tr, _ = rasters.shape
            diffs_samp = exhs - inhs
            diffs_ms = self.session.samples_to_millis(diffs_samp)
            order = np.argsort(diffs_ms)
            diffs_ms_ordered = diffs_ms[order]
            points = [(0, 1)]  # start at trial 1, not 0 consistent with how we're plotting the rasters.
            points.extend([(diffs_ms_ordered[x], x + 1) for x in range(n_tr)])
            points.append((0, n_tr))
            poly = Polygon(points, color='g', alpha=.25)
            # TODO: polygon is having trouble covering the points of trial 1. not sure why.
            rasters = rasters[order]
        ax = self.plot_rasters(rasters, x, axis=axis, quick_plot=quick_plot, color=color, alpha=alpha,
                               offset=offset, markersize=markersize)
        if sniff_overlay:
            ax.add_patch(poly)
        return ax

    def plot_odor_rasters_warp(self, odor, concentration, pre_ms, post_ms, sort_sniffs=True, axis=None,
                               quick_plot=True, color=None, alpha=1, offset=0, markersize=5):
        """
        Plots odor response rasters in time that is warped to duration of first inhalation.

        Warping is of the ith spike in the jth trial:
            spiketime[i, j] *= ( µ_inhale_duration / inhalation_duration[j] )

        :param odor: odor name (str)
        :param concentration: odor concentration to plot.
        :param pre_ms: number of milliseconds prior to inhalation to plot
        :param post_ms: number of milliseconds after inhalation to plot
        :param sort_sniffs: if True (default), sort the sniffs by the inhalation length.
        :param axis: matplotlib axis on which to plot (optional: otherwise, make new axis)
        :param quick_plot: if False, use a much slower rasterization method that draws lines instead of just using dots.
        :param color: matplotlib colorspec for psth line (ie 'k' for black line)
        :param alpha: transparency of psth line (float: 1. is opaque, 0. is transparent)
        :param offset: used for plotting muliple conditions on same axis.
        :param markersize: size of marker to use for plotting.
        :return: plot axis
        """
        inhs, exhs = self.session.get_first_odor_sniffs(odor, concentration)
        ntrials = len(inhs)
        diffs = exhs - inhs
        if sort_sniffs:
            # sort inhalation onsets, exhalations and diffs by inhalation length. Rasters will be ordered
            sort_indexes = np.argsort(diffs)
            inhs, exhs, diffs = [x[sort_indexes] for x in (inhs, exhs, diffs)]
        scalars = diffs.mean() / diffs
        pre, post = self.session.millis_to_samples((pre_ms, post_ms))
        allspikes = []
        alltrials = []
        for i in range(ntrials):
            t_0 = inhs[i]
            st = t_0 - pre  # constant.
            s = scalars[i]
            spikes = (self.get_epoch_samples(st, t_0 + post / s) - st).astype(
                float)  # subtract start before converting from uint to not lose precision
            spikes -= pre  # subtract to get negative pre-t0 times AFTER conversion to float because uint will break
            spikes[spikes > 0] *= s  # scaling floats is right thing to do here.
            allspikes.append(spikes)
            alltrials.extend([i] * len(spikes))  # start at trial '1'
        allspikes = np.concatenate(allspikes)
        alltrials = np.array(alltrials)
        allspikes_ms = self.session.samples_to_millis(allspikes)
        return self.plot_rasters((alltrials, allspikes_ms, ntrials), axis=axis, quick_plot=quick_plot, color=color, alpha=alpha,
                                 offset=offset, markersize=markersize)


class OdorSession(Session):
    unit_type = OdorUnit

    def __init__(self, meta_fn):
        super(OdorSession, self).__init__(meta_fn)

    def _make_stimuli(self, meta_file: tb.File) -> dict:
        """

        :param meta_f: tb.File object.
        :return: stimulus dictionary
        """
        events = meta_file.root.Events
        trials = meta_loaders.load_aligned_trials(meta_file)
        fv = events.finalvalve.read()
        fv_starts = fv[:, 0]
        fv_ends = fv[:, 1]
        n_trs = len(trials)
        self.inhales, self.exhales = meta_loaders.load_sniff_events(meta_file)

        # building lists of stimulus attributes. Doing this because masking is easy/efficient with np arrays.
        odors_by_stim = []
        inhales_by_stim = []
        exhales_by_stim = []
        finalvalve_on_times = []
        finalvalve_off_times = []
        concentrations_by_stim = []

        _odor_fields_valid = []
        for jj in range(4):  # check for olfa nodes for subsequent extractions.
            s = ODOR_FIELD.format(jj)
            if s in trials[0][1].dtype.names:
                _odor_fields_valid.append(s)
        # todo: get odor concentrations from multiple olfactometers!!!!
        for i_tr in range(n_trs - 1):
            nd = trials[i_tr + 1][0]
            st, tr = trials[i_tr]
            odors_by_olfa = []
            for s in _odor_fields_valid:
                odor = tr[s]
                if odor:
                    odors_by_olfa.append(odor.decode())  # make into string from binary.
            assert len(odors_by_olfa) < 2, 'multiple concentrations functionality is not included.'
            if len(odors_by_olfa):
                odor = odors_by_olfa[0]
            else:
                odor = 'blank'
            cs = tr[ODOR_CONC_FIELD]
            fv_starts_trial_mask = (fv_starts > st) & (fv_starts < nd)
            if fv_starts_trial_mask.any():
                # if there is no final valve opening, we don't want to add anything to the stim dictionary.
                if fv_starts_trial_mask.sum() > 1:
                    print('warning, there are more than 1 final valve openings in trial.')
                # we're taking only the first fvon, any additional are due to trial numbering problems.
                fvon = fv_starts[fv_starts_trial_mask][0]
                fvoff = fv_ends[fv_ends > fvon][0]  # first end following start is the end.
                # process inhalations and exhalations falling within the stimulus time period.
                inhs_fv = np.array([])
                exhs_fv = np.array([])
                inhale_mask_fv = ((self.inhales > fvon) & (self.inhales < fvoff))
                if inhale_mask_fv.any():
                    inhs_fv = self.inhales[inhale_mask_fv]
                    inh_idxes = np.where(inhale_mask_fv)[0]
                    first_inh, last_inh = inhs_fv.min(), inhs_fv.max()
                    num_inhs = len(inh_idxes)
                    exh_mask = self.exhales > first_inh
                    if exh_mask.any():
                        first_exh_idx = np.where(exh_mask)[0][0]
                        last_exh_idx = min((first_exh_idx + num_inhs + 1, len(self.exhales)))
                        exhs_fv = self.exhales[first_exh_idx:last_exh_idx]  # n inhales == n exhales

                # these appends only happen if FV opening is detected:
                inhales_by_stim.append(inhs_fv)
                exhales_by_stim.append(exhs_fv)
                finalvalve_on_times.append(fvon)
                finalvalve_off_times.append(fvoff)
                odors_by_stim.append(odor)
                concentrations_by_stim.append(cs)

        result = {
            'fv_ons': np.array(finalvalve_on_times),
            'fv_offs': np.array(finalvalve_off_times),
            'odors': np.array(odors_by_stim),
            'odorconcs': np.array(concentrations_by_stim),
            'inhales': np.array(inhales_by_stim),
            'exhales': np.array(exhales_by_stim)
        }
        return result

    def get_odors(self) -> np.array:
        """
        Returns all odors found within the session.
        """
        return np.unique(self.stimuli['odors'])

    def get_concentrations(self, odor: str) -> np.array:
        """
        Returns a sorted array of unique concentrations presented for a specified odorant.
        """
        odors = self.stimuli['odors']
        odormask = odors == odor
        concs = self.stimuli['odorconcs']
        return np.unique(concs[odormask])

    def get_first_odor_sniffs(self, odor: str, concentration):
        """
        returns the first inhalations and exhalations of specified odorant.

        :param odor: string specifying odor
        :param concentration: numeric specifying concentration of odor.
        :return: tuple (inhalations, exhalations) of arrays.
        """

        odors = self.stimuli['odors']
        concs = self.stimuli['odorconcs']
        inhales = self.stimuli['inhales']
        exhales = self.stimuli['exhales']
        odormask = odors == odor
        concmask = concs == concentration
        allmask = odormask & concmask
        idxes = np.where(allmask)[0]
        first_inhs, first_exhs = [], []
        for i in idxes:
            inhs, exhs = inhales[i], exhales[i]
            if len(inhs) and len(exhs):
                first_inhs.append(inhs[0])
                first_exhs.append(exhs[0])
        return np.array(first_inhs), np.array(first_exhs)

    def get_sniff(self) -> np.array:
        """
        loads all sniff samples from the session meta file.
        """
        with tb.open_file(self.filenames['meta'], 'r') as f:
            sniff = meta_loaders.load_sniff_trace(f)
            # todo: think about cacheing this because there are cases where you're going to want to extract many snippets at different times.
        return sniff

    def get_sniff_traces(self, t_0s, pre_ms, post_ms) -> np.ndarray:
        """
        Loads and returns sniff sample values around specified sniff t_0s.

        :param t_0s: array or list of t_0s.
        :param pre_ms: number of ms to return prior to specified t_0s.
        :param post_ms: number of ms to return after specified t_0s.
        :return: sniffs in 2d array (Nsniffs, Nsamples) (C-order)
        """

        pre_samps, post_samps = self.millis_to_samples((pre_ms, post_ms))

        if np.isscalar(t_0s):
            n_sniffs = 1
            t_0s = np.array([t_0s])
        else:
            n_sniffs = len(t_0s)

        with tb.open_file(self.filenames['meta'], 'r') as f:
            sniff = meta_loaders.load_sniff_trace(f)
        sniff_mat = np.zeros((n_sniffs, int(pre_samps + post_samps)), dtype=sniff.dtype)
        for i in range(n_sniffs):
            t = t_0s[i]
            st = int(t - pre_samps)
            nd = int(t + post_samps)
            sniff_mat[i, :] = sniff[st:nd]
        return sniff_mat

    def plot_sniffs(self, t_0s, pre_ms, post_ms, color='b', alpha=1., linewidth=2, linestyle='-'):
        """
        Plots sniff trace around times specified by t_0s (specified in samples)

        :param t_0s: array or list of t_0s specified in *samples*
        :param pre_ms: number of ms to return prior to specified t_0s.
        :param post_ms: number of ms to return after specified t_0s.
        :param color: matplotlib colorspec for the psth line (ie "k" for a black line)
        :param alpha: transparency of psth line (float: 1. is opaque, 0. is transparent)
        :param linewidth: line width for psth plot (float)
        :param linestyle: matplotlib linespec for psth plot
        :return:
        """

        sniffs = self.get_sniff_traces(t_0s, pre_ms, post_ms)
        x = np.linspace(-pre_ms, post_ms, num=len(sniffs.T))
        # todo: accept existing axis.
        for i in range(len(sniffs)):
            plt.plot(x, sniffs[i, :], color=color, linestyle=linestyle, linewidth=linewidth, alpha=alpha)
        plt.plot([0] * 2, plt.ylim(), '--k', linewidth=1)
        return

    def plot_odor_sniffs(self, odor: str, conc, pre_ms, post_ms, separate_plots=False, color='b', alpha=1.,
                         linewidth=2, linestyle='-', ):
        """
        Plots sniffs around the first inhalation of odor.

        :param odor: odor specification
        :param conc: odor concentration specification
        :param pre_ms: number of ms to return prior to specified t_0s.
        :param post_ms: number of ms to return after specified t_0s.
        :param separate_plots: if True, plot each sniff on separate axis with inhalation and exhalation marked.
        :param color: matplotlib colorspec for the psth line (ie "k" for a black line)
        :param alpha: transparency of psth line (float: 1. is opaque, 0. is transparent)
        :param linewidth: line width for psth plot (float)
        :param linestyle: matplotlib linespec for psth plot
        :return:
        """

        inhs, exhs = self.get_first_odor_sniffs(odor, conc)
        if separate_plots:
            for i in range(len(inhs)):
                self.plot_sniffs(inhs[i], pre_ms, post_ms, color=color, alpha=alpha, linestyle=linestyle,
                                 linewidth=linewidth)
                plt.plot([self.samples_to_millis(exhs[i] - inhs[i])] * 2, plt.ylim())
                plt.show()
        else:
            self.plot_sniffs(inhs, pre_ms, post_ms, color=color, alpha=alpha, linestyle=linestyle,
                             linewidth=linewidth)
        return
