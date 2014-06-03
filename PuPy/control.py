"""
Change log:
2014-05-06: TerrainCollector is added.
2014-05-06: Bug fixed in TumbleCollector and ResetCollector (index of epoch was calculated wrongly).
2014-05-06: load_gaits returns a list of Gait instances also when no names are given.
2014-05-07: changed the signal chain to start with top-most Actor and propagate down the childs afterwards.
2014-05-07: included a reset-listening option in _RobotCollector_h5py.
2014-05-07: _RobotCollector_h5py recognizes dtype of data.
"""
import random
from math import sin, pi
import time
import numpy as np
import json
import PuPy
from PuPy.terrains import get_terrain_index_from_position, read_terrain_index
import os
import warnings

def load_gaits(filename=PuPy.__file__[:-17]+'data'+os.sep+'puppy_gaits.json', names=None):
    """
    Returns the gaits stored in the given json file.

    ``filename``
        string containing the name of the json file where the gaits are stored
        (default is PuPy/data/puppy_gaits.json).

    ``names``
         If the gait names are given as a list of strings, only the desired gaits are returned as a list.
         Otherwise a list containing all gaits are returned.
    """
    gaits = json.load(open(filename, 'r'))
    if names is None:
        names = gaits
    gaits = [Gait(gaits[name], name) for name in names]
    return gaits


class Gait(object):
    """Motor target generator, using predefined gait_switcher.

    The motor signal follows the parametrised sine

        :math:`A  \sin(2 \pi f x + p) + B`

    with the parameters A, f, p, B

    ``params``
        :py:meth:`dict` holding the parameters:

         keys:   amplitude, frequency, phase, offset

         values: 4-tuples holding the parameter values. The order is
                 front-left, front-right, rear-left, rear-right

    """
    def __init__(self, params, name=None):
        self.params = params
        if name is None:
            name = 'Unknown gait'
        self.name = name

    def __iter__(self):
        return self.iter(0, 20)

    def iter(self, time_start_ms, step):
        """Return the motor target sequence in the interval [*time_start_ms*, *time_end_ms*]."""
        params = zip(self.params['amplitude'], self.params['frequency'], self.params['phase'], self.params['offset'])
        current_time_ms = time_start_ms
        while True:
            current_time_ms += step
            yield [A * sin(2.0 * pi * (freq * current_time_ms / 1e3 - phase)) + offset for A, freq, phase, offset in params]

    def copy(self):
        return Gait(self.params.copy(), self.name)

    def __str__(self):
        return self.name


class NoneChild(object):
    """Dummy class for actor's child."""
    def __call__(self, epoch, time_start_ms, time_end_ms, step_size_ms):
        return None
    def _get_initial_targets(self, time_start_ms, time_end_ms, step_size_ms):
        return None

class RobotActor(object):
    """Template class for an actor, used in :py:class:`WebotsPuppyMixin`.

    The actor is called after every control period, when a new sequence
    of motor targets is required. It is expected to return an iterator
    which in every step produces a 4-tuple, representing the targets
    of the motors.
    The order is front-left, front-right, rear-left, rear-right.

    ``epoch``
        The sensor measurements in the previous control period. They
        are returned as dict, with the sensor name as key and a
        numpy array of observations as value.

        Note that the motor targets are the ones that have been applied,
        i.e. those that lead up to the sensor measurements. Imagine this
        cycle::

            trg[i] -> move robot -> sensors[i] -> trg[i+1] -> ...

        Further, note that the :py:meth:`dict` may be empty (this is
        guaranteed at least once in the simulator initialization).

    ``time_start_ms``
        The (simulated) time from which on the motor target will be
        applied. *time_start_ms* is weakly positive and strictly
        monotonic increasing (meaning that it is zero only in the very
        first call).

    ``time_end_ms``
        The (simulated) time up to which the motor target must at least
        be defined.

    ``step_size_ms``
        The motor period, i.e. the number of milliseconds pass until
        the next motor target is applied.

    If the targets are represented by a list, it must at least have

        .. :math:`\\frac{ \\text{time_end_ms} - \\text{time_start_ms} }{ \\text{step_size_ms} }`

        (*time_end_ms* - *time_start_ms*) / *step_size_ms*

    items and it has to be returned as iterator, as in

        >>> iter(myList)

    ``child``
        Every :py:class:`RobotActor` has a ``child`` member, which can be
        another :py:class:`RobotActor`. The ``child`` is called in the end
        of the __call__() method,
        e.g. return self.child(epoch, time_start_ms, time_end_ms, step_size_ms).

    """
    def __init__(self, child=None, verbose=False):
        self.verbose = verbose
        self.child = child
        if self.child is None:
            self.child = NoneChild()

    def __call__(self, epoch, time_start_ms, time_end_ms, step_size_ms):
        raise NotImplementedError()

    def __getattr__(self, name):
        """if a ``self`` does not have member ``name``, try to get it from child."""
        if name is 'child': # prevent infinite recursion when looking up non-existing child
            raise AttributeError()
        return getattr(self.child, name)

    def get_from_child(self, name, default=None):
        """DEPRICATED. Try to use normal method getattr (x.name)!
        Return member ``name`` from first child class in the hierarchy that has this member."""
        if hasattr(self.child, name):
            return getattr(self.child, name)
        elif hasattr(self.child, 'get_from_child'):
            return self.child.get_from_child(name)
        else:
            return default

    def signal(self, msg, **kwargs):
        """broadcast signal message ``msg`` to all child classes."""
        self._signal(msg, **kwargs)
        if hasattr(self.child, 'signal'):
            self.child.signal(msg, **kwargs)

    def _signal(self, msg, **kwargs):
        """Template method for subclasses. Overload this method to enable signal receiving."""
        pass

    def _get_initial_targets(self, time_start_ms, time_end_ms, step_size_ms):
        """Template method for subclasses. Use this to return a motor targets-iterator at time 0."""
        if hasattr(self.child, '_get_initial_targets'):
            return self.child._get_initial_targets(time_start_ms, time_end_ms, step_size_ms)
        return None


class PuppyActor(RobotActor):
    """Deprecated alias for :py:class:`RobotActor`."""
    pass


class RandomGaitControl(RobotActor):
    """From a list of available gaits, randomly select one."""
    def __init__(self, gaits):
        super(RandomGaitControl, self).__init__()
        self.gaits = gaits[:]
        self.gait = None

    def __call__(self, epoch, time_start_ms, time_end_ms, step_size_ms):
        self.gait = random.choice(self.gaits)
        print self.gait
        return self.gait.iter(time_start_ms, step_size_ms)

    def _get_initial_targets(self, time_start_ms, time_end_ms, step_size_ms):
        return self.__call__(None, time_start_ms, time_end_ms, step_size_ms)


class ConstantGaitControl(RobotActor):
    """Given a gait, always apply it."""
    def __init__(self, gait):
        super(ConstantGaitControl, self).__init__()
        self.gait = gait

    def __call__(self, epoch, time_start_ms, time_end_ms, step_size_ms):
        return self.gait.iter(time_start_ms, step_size_ms)

    def _get_initial_targets(self, time_start_ms, time_end_ms, step_size_ms):
        return self.__call__(None, time_start_ms, time_end_ms, step_size_ms)


class SequentialGaitControl(RobotActor):
    """Execute a predefined sequence of gaits.

    Note that it's assumed that *gait_iter* does not terminate
    permaturely.
    """
    def __init__(self, gait_iter):
        super(SequentialGaitControl, self).__init__()
        self.gait_iter = gait_iter
        self.gait = None

    def __call__(self, epoch, time_start_ms, time_end_ms, step_size_ms):
        self.gait = self.gait_iter.next()
        return self.gait.iter(time_start_ms, step_size_ms)

    def _get_initial_targets(self, time_start_ms, time_end_ms, step_size_ms):
        return self.__call__(None, time_start_ms, time_end_ms, step_size_ms)


class _RobotCollector_h5py(RobotActor):
    """Collect sensor readouts and store them in a file.
    HDF5 is written through the h5py module.

    The data is stored in the [HDF5]_ format. For each simulation run,
    there's a group, identified by a running number. Within each group,
    the sensor data is stored in exclusive datasets, placed under the
    sensor's name.

    ``child``
        The :py:class:`PuppyCollector` works as intermediate actor, it
        does not implement a policy itself. For this, another ``child``-actor
        is required. It must match the :py:class:`RobotActor` interface.

    ``expfile``
        Path to the file into which the experimental data should be
        stored.

    ``headers``
        Additional headers, stored with the current experiment.
        A *dict* is expected. Default is None (no headers).

    """
    def __init__(self, child, expfile, headers=None, vars=None, warn=True, new_episode_on_reset=False):
        super(_RobotCollector_h5py, self).__init__(child)

        self.headers = headers
        self.vars = vars
        self.warn = warn
        self.new_episode_on_reset = new_episode_on_reset
        self.new_episode = False
        
        # create experiment storage
        import h5py
        self.fh = h5py.File(expfile,'a')
        self._create_group(str(len(self.fh.keys())))

    def _create_group(self, grp_name):
        self.grp_name = grp_name
        self.grp = self.fh.create_group(self.grp_name)
        self.set_header('time', time.ctime())
        if self.headers is not None:
            for k in self.headers:
                self.set_header(k, self.headers[k])
        print "Using storage", self.grp_name

    def set_header(self, name, data):
        """Add custom header ``data`` to the current group. The data is
        stored under key ``name``. If the key is already in use, the
        value will be overwritten.
        """
        import h5py
        amngr = h5py.AttributeManager(self.grp)
        if name in amngr:
            del amngr[name]
        amngr.create(name, data)

    def __del__(self):
        self.fh.close()

    # if RevertTumbling is used:
    #  last epoch will not be written since it is not necessarily complete;
    #  grace time deals with this (> one epoch)
    def __call__(self, epoch, time_start_ms, time_end_ms, step_size_ms):
        # write epoch to dataset
        keys = epoch.keys()
        if self.vars is None:
            vars = keys
        else:
            vars = self.vars
        for k in vars:
            if k not in keys:
                if self.warn:
                    warnings.warn('logging of %s requested but not present in epoch. skipping...'%k)
                continue
            if k not in self.grp:
                maxshape = tuple([None] * len(epoch[k].shape))
                self.grp.create_dataset(k, data=epoch[k], chunks=True, maxshape=maxshape, dtype=epoch[k].dtype)
            else:
                N = epoch[k].shape[0]
                K = self.grp[k].shape[0]
                self.grp[k].resize(size=N+K, axis=0)
                self.grp[k][K:] = epoch[k]

        self.fh.flush()
        
        if self.new_episode:
            self.new_episode = False
            self._create_group(str(int(self.grp_name)+1))
        return self.child(epoch, time_start_ms, time_end_ms, step_size_ms)

    def _signal(self, msg, **kwargs):
        super(_RobotCollector_h5py, self)._signal(msg, **kwargs)
        if isinstance(msg, str) and msg=='new_episode' or (msg=='reset' and self.new_episode_on_reset):
            # start a new episode, i.e. create a new group in expfile
            # and store all new epochs there.
            # But do it after next control step:
            self.new_episode = True


class _RobotCollector_pytables(RobotActor):
    """Collect sensor readouts and store them in a file.
    HDF5 is written through the PyTables module.

    The data is stored in the [HDF5]_ format. For each simulation run,
    there's a group, identified by a running number. Within each group,
    the sensor data is stored in exclusive datasets, placed under the
    sensor's name.

    ``child``
        The :py:class:`PuppyCollector` works as intermediate actor, it
        does not implement a policy itself. For this, another ``child``-actor
        is required. It must match the :py:class:`RobotActor` interface.

    ``expfile``
        Path to the file into which the experimental data should be
        stored.

    ``headers``
        Additional headers, stored with the current experiment.
        A *dict* is expected. Default is None (no headers).

    """
    def __init__(self, child, expfile, headers=None):
        super(_RobotCollector_pytables, self).__init__(child)

        # create experiment storage
        import tables
        self.fh = tables.File(expfile,'a')
        name = 'exp' + str(len(self.fh.root._v_groups))
        self.grp = self.fh.create_group(self.fh.root, name)

        self.grp._f_setattr('time', time.time())
        if headers is not None:
            for k in headers:
                self.grp._f_setattr(k, headers[k])

        print "Using storage", name

    def __del__(self):
        self.fh.close()

    def set_header(self, name, data):
        """Add custom header ``data`` to the current group. The data is
        stored under key ``name``. If the key is already in use, the
        value will be overwritten.
        """
        self.grp._f_setattr(name, data)

    # if RevertTumbling is used:
    #  last epoch will not be written since it is not necessarily complete;
    #  grace time deals with this (> one epoch)
    def __call__(self, epoch, time_start_ms, time_end_ms, step_size_ms):
        # write epoch to dataset
        for k in epoch:
            if k not in self.grp:
                self.fh.create_earray(self.grp, k, chunkshape=epoch[k].shape, obj=epoch[k])
            else:
                self.grp._v_children[k].append(epoch[k])

        self.fh.flush()
        return self.child(epoch, time_start_ms, time_end_ms, step_size_ms)

    def _signal(self, msg, **kwargs):
        super(_RobotCollector_pytables, self)._signal(msg, **kwargs)
        if isinstance(msg, str) and msg=='new_episode':
            # start a new episode, i.e. create a new group in expfile
            # and store all new epochs there:
            warnings.warn("starting new episode not yet implemented in ``_RobotCollector_pytables``.")


class RobotCollector(_RobotCollector_h5py):
    """Collect sensor readouts and store them in a file.

    The data is stored in the [HDF5]_ format. For each simulation run,
    there's a group, identified by a running number. Within each group,
    the sensor data is stored in exclusive datasets, placed under the
    sensor's name.

    .. note::
        This class abstracts interface from implementation. Internally,
        either the HDF5 interface from [PyTables]_ or [h5py]_ may be
        used.

    ``child``
        The :py:class:`PuppyCollector` works as intermediate actor, it
        does not implement a policy itself. For this, another ``child``-actor
        is required. It must match the :py:class:`RobotActor` interface.

    ``expfile``
        Path to the file into which the experimental data should be
        stored.

    ``headers``
        Additional headers, stored with the current experiment.
        A *dict* is expected. Default is None (no headers).
    """
    pass


class PuppyCollector(RobotCollector):
    """Deprecated alias for :py:class:`RobotCollector`."""
    pass


class GaitNameCollector(RobotActor):
    """A collector that records the name of the current gait."""
    def __init__(self, child, sampling_period_ms, ctrl_period_ms, gait_names=None, **kwargs):
        super(GaitNameCollector, self).__init__(child, **kwargs)
        self.sampling_period_ms = sampling_period_ms
        self.ctrl_period_ms = ctrl_period_ms
        if gait_names is not None:
            self.max_name_len = max([len(gait) for gait in gait_names])
        else:
            self.max_name_len = 20

    def __call__(self, epoch, time_start_ms, time_end_ms, step_size_ms):
        if time_start_ms:
            gait_name = self.gait.name
            epoch['gait'] = np.repeat(np.array(gait_name, dtype='|S'+str(self.max_name_len)), self.ctrl_period_ms/self.sampling_period_ms)
        return self.child(epoch, time_start_ms, time_end_ms, step_size_ms)


class GaitIndexCollector(RobotActor):
    """
    A collector that records the index of the current gait.
    The mapping between indices and names of the gaits is stored in a dictionary in the
    header of the log file (requires a collector with method 'set_header(name, data)').
    """
    def __init__(self, child, sampling_period_ms, ctrl_period_ms, gait_names=None, **kwargs):
        super(GaitIndexCollector, self).__init__(child, **kwargs)
        self.sampling_period_ms = sampling_period_ms
        self.ctrl_period_ms = ctrl_period_ms
        if gait_names is None:
            self.gait_names = []
        else:
            self.gait_names = gait_names
            self._set_header(self.gait_names)

    def _set_header(self, gait_names):
        if hasattr(self, 'set_header'):
            self.set_header('gait_names', np.array(map(str,gait_names)))

    def __call__(self, epoch, time_start_ms, time_end_ms, step_size_ms):
        if time_start_ms:
            gait_name = self.gait.name
            if gait_name not in self.gait_names:
                self.gait_names.append(gait_name)
                self._set_header(self.gait_names)
            gait_idx = np.nonzero(np.array(self.gait_names)==gait_name)[0][0]
            epoch['gait_idx'] = np.repeat([gait_idx], self.ctrl_period_ms/self.sampling_period_ms)
        return self.child(epoch, time_start_ms, time_end_ms, step_size_ms)


class GaitParametersCollector(RobotActor):
    """A collector that records the motor parameters."""
    def __init__(self, child, sampling_period_ms, ctrl_period_ms, **kwargs):
        super(GaitParametersCollector, self).__init__(child, **kwargs)
        self.sampling_period_ms = sampling_period_ms
        self.ctrl_period_ms = ctrl_period_ms

    def __call__(self, epoch, time_start_ms, time_end_ms, step_size_ms):
        if time_start_ms:
            current_params = self.gait.params
            epoch['frequency_FL'] = np.repeat([current_params['frequency'][0]], self.ctrl_period_ms/self.sampling_period_ms)
            epoch['frequency_FR'] = np.repeat([current_params['frequency'][1]], self.ctrl_period_ms/self.sampling_period_ms)
            epoch['frequency_HL'] = np.repeat([current_params['frequency'][2]], self.ctrl_period_ms/self.sampling_period_ms)
            epoch['frequency_HR'] = np.repeat([current_params['frequency'][3]], self.ctrl_period_ms/self.sampling_period_ms)
            epoch['offset_FL'] = np.repeat([current_params['offset'][0]], self.ctrl_period_ms/self.sampling_period_ms)
            epoch['offset_FR'] = np.repeat([current_params['offset'][1]], self.ctrl_period_ms/self.sampling_period_ms)
            epoch['offset_HL'] = np.repeat([current_params['offset'][2]], self.ctrl_period_ms/self.sampling_period_ms)
            epoch['offset_HR'] = np.repeat([current_params['offset'][3]], self.ctrl_period_ms/self.sampling_period_ms)
            epoch['amplitude_FL'] = np.repeat([current_params['amplitude'][0]], self.ctrl_period_ms/self.sampling_period_ms)
            epoch['amplitude_FR'] = np.repeat([current_params['amplitude'][1]], self.ctrl_period_ms/self.sampling_period_ms)
            epoch['amplitude_HL'] = np.repeat([current_params['amplitude'][2]], self.ctrl_period_ms/self.sampling_period_ms)
            epoch['amplitude_HR'] = np.repeat([current_params['amplitude'][3]], self.ctrl_period_ms/self.sampling_period_ms)
            epoch['phase_FL'] = np.repeat([current_params['phase'][0]], self.ctrl_period_ms/self.sampling_period_ms)
            epoch['phase_FR'] = np.repeat([current_params['phase'][1]], self.ctrl_period_ms/self.sampling_period_ms)
            epoch['phase_HL'] = np.repeat([current_params['phase'][2]], self.ctrl_period_ms/self.sampling_period_ms)
            epoch['phase_HR'] = np.repeat([current_params['phase'][3]], self.ctrl_period_ms/self.sampling_period_ms)
        return self.child(epoch, time_start_ms, time_end_ms, step_size_ms)


class TumbleCollector(RobotActor):
    """A collector that records when Puppy tumbles."""
    def __init__(self, child, sampling_period_ms, ctrl_period_ms, **kwargs):
        super(TumbleCollector, self).__init__(child, **kwargs)
        self.sampling_period_ms = sampling_period_ms
        self.ctrl_period_ms = ctrl_period_ms
        self._tumbled = np.zeros([self.ctrl_period_ms/self.sampling_period_ms,], dtype=bool)
        self.event_handler = lambda a,b,c,d:None # The usage of event_handler() is DEPRICATED! Use signal(msg, **kwargs) instead.

    def __call__(self, epoch, time_start_ms, time_end_ms, step_size_ms):
        if time_start_ms:
            epoch['tumble'] = self._tumbled
            self._tumbled = np.zeros([self.ctrl_period_ms/self.sampling_period_ms,], dtype=bool)
        return self.child(epoch, time_start_ms, time_end_ms, step_size_ms)

    def _signal(self, msg, **kwargs):
        super(TumbleCollector, self)._signal(msg, **kwargs)
        if msg=='tumbled':
            current_time = kwargs['current_time']
            self._tumbled[(current_time/self.sampling_period_ms) % (self.ctrl_period_ms/self.sampling_period_ms) - 1] = True


class ResetCollector(RobotActor):
    """A collector that records when Puppy was reset (respawned)."""
    def __init__(self, child, sampling_period_ms, ctrl_period_ms, **kwargs):
        super(ResetCollector, self).__init__(child, **kwargs)
        self.sampling_period_ms = sampling_period_ms
        self.ctrl_period_ms = ctrl_period_ms
        self._reset = np.zeros([self.ctrl_period_ms/self.sampling_period_ms,], dtype=bool)
        self.event_handler = lambda a,b,c,d:None # The usage of event_handler() is DEPRICATED! Use signal(msg, **kwargs) instead.

    def __call__(self, epoch, time_start_ms, time_end_ms, step_size_ms):
        if time_start_ms:
            epoch['reset'] = self._reset
            self._reset = np.zeros([self.ctrl_period_ms/self.sampling_period_ms,], dtype=bool)
        return self.child(epoch, time_start_ms, time_end_ms, step_size_ms)

    def _signal(self, msg, **kwargs):
        super(ResetCollector, self)._signal(msg, **kwargs)
        if msg=='reset':
            current_time = kwargs['current_time']
            self._reset[(current_time/self.sampling_period_ms) % (self.ctrl_period_ms/self.sampling_period_ms) - 1] = True


class TerrainCollector(RobotActor):
    """Need either the terrain_file to extract the current terrain from GPS,
    or needs the sampling- and control periods and a supervisor that signals the terrain idx.
    """
    def __init__(self, child, terrain_file=None, sampling_period_ms=None, ctrl_period_ms=None, **kwargs):
        super(TerrainCollector, self).__init__(child, **kwargs)
        if terrain_file is None:
            self.has_supervisor = True
            self.sampling_period_ms = sampling_period_ms
            self.fs_ratio = ctrl_period_ms/self.sampling_period_ms
            self._terrain = np.ones([self.fs_ratio,], dtype=int) * (-1)
        else:
            self.has_supervisor = False
            self.terrain_idx, self.terrain_size, self.patch_size, _ = read_terrain_index(terrain_file)
        self.current_terrain = -1
    
    def __call__(self, epoch, time_start_ms, time_end_ms, step_size_ms):
        if self.has_supervisor:
            if time_start_ms:
                epoch['terrain_idx'] = self._terrain
                self._terrain = np.ones([self.fs_ratio,], dtype=int) * (-1)
            #print 'epoch terrains:', len(epoch['terrain_idx']), '\n', epoch['terrain_idx']
        else:
            position = zip(epoch['puppyGPS_x'], epoch['puppyGPS_y'])
            epoch['terrain_idx'] = np.empty(len(position), dtype=int)
            for i,pos in enumerate(position):
                epoch['terrain_idx'][i] = get_terrain_index_from_position(pos, self.terrain_idx, self.terrain_size, self.patch_size)
            #print 'epoch terrains:\n', '\n'.join(map(str, zip(position, epoch['terrain_idx'])))
        return self.child(epoch, time_start_ms, time_end_ms, step_size_ms)
    
    def _signal(self, msg, **kwargs):
        super(TerrainCollector, self)._signal(msg, **kwargs)
        if self.has_supervisor:
            if msg[:8]=='terrain=':
                current_terrain = int(msg[8:])
                if current_terrain != self.current_terrain:
                    print 'terrain is', current_terrain
                    self.current_terrain = current_terrain
                current_time = kwargs['current_time']
                #print 'time=',current_time, (current_time/self.sampling_period_ms) % self.fs_ratio-1, self.current_terrain, msg[10:]
                self._terrain[(current_time/self.sampling_period_ms) % self.fs_ratio - 1] = current_terrain # note that last sample of epoch has index -1


class OnlinePrinter(RobotActor):
    """An actor that prints out desired variables from the epoch."""
    def __init__(self, child, var_list=[], separator=', ', **kwargs):
        RobotActor.__init__(self, child, **kwargs)
        self.var_list = var_list
        self.separator = separator
        import sys
        self.sys = sys

    def __call__(self, epoch, time_start_ms, time_end_ms, step_size_ms):
        s = ''
        for v in self.var_list:
            x = lambda a:a
            if isinstance(v, list) and callable(v[1]):
                x = v[1]
                v = v[0]
            else:
                x = lambda a:a
            if not v in epoch:
                warnings.warn('variable "%s" not in epoch. skipping...'%v)
                continue
            s += v + ': ' + str( x(epoch[v]) )
            s += self.separator
        if len(self.separator)>0:
            s = s[:-len(self.separator)]
        if len(s)>0:
            print s
            self.sys.stdout.flush()
        return self.child(epoch, time_start_ms, time_end_ms, step_size_ms)


#import sys
#import multiprocessing

class OnlinePlotter(RobotActor):
    """An actor that plots data while the robot is running.

    .. warning: Highly experimental code, is not properly working!

    Known issues:
    * Thread is not properly cancelled. This might be because when you press 'Stop' in webots, the thread stops (hence can't be joined). This also interferes with simulation restarting. Hotfix: After termination, use `pkill -f python.*<dir name>.*robot\.py`
    * Window redrawing is only done in an update, it's not a detached process itself. Hence reactivity is poor and also the window buttons can't be used (change view or save image).
    * Multiprocessing approach doesn't open a window
    * First episode is not plotted
    """
    def __init__(self, child, var_list, max_window_len=300, window_fs=0.25, **kwargs):
        assert len(var_list) > 0
        super(OnlinePlotter, self).__init__(child, **kwargs)

        import threading
        import Queue
        self.max_window_len = max_window_len
        self.window_fs = window_fs
        self._thread_cancelled = False
        #self._thread_queue = multiprocessing.Queue(1)
        #self._thread_handle = multiprocessing.Process(target=self._plotter_loop, args=(self, var_list,))
        self._thread_queue = Queue.Queue(1)
        self._thread_handle = threading.Thread(target=self._plotter_loop, name='plotter_loop', args=(self, var_list,))
        self._thread_handle.start()
        while not self._thread_handle.is_alive():
            pass

    def _plotter_loop(dummy_self, self, var_list):
        #out = open('/tmp/pout.t', 'w')
        #out = sys.stdout
        #out.write('Thread started with args' + str(var_list) + '\n')
        #out.flush()
        import pylab as pl
        import Queue
        pl.ion()
        initialized = False
        while not self._thread_cancelled:
            try:
                #out.write('Waiting for data\n')
                #out.flush()
                epoch, time_start, time_end, step = self._thread_queue.get(True, self.window_fs)
            except Queue.Empty:
                continue
            #out.write('Got data, updating plot\n')
            #out.flush()
            if not initialized:
                fig, ax = pl.subplots(len(var_list), 1, True)
                if len(var_list) == 1:
                    ax = [ax]
                line = []
                for i,v in enumerate(var_list):
                    if isinstance(v, (list,tuple)):
                        v = v[0]+'[%d]'%v[1]
                        n_lines = 1
                    else:
                        n_lines = epoch[v].shape[1]
                    ax[i].set_ylabel(v)
                    line.append([ax[i].plot([],[])[0] for _ in range(n_lines)])
                ax[-1].set_xlabel('time')
                initialized = True
                

            for i,v in enumerate(var_list):
                if isinstance(v, (list,tuple)):
                    dat = epoch[v[0]][:,v[1]:v[1]+1]
                else:
                    dat = epoch[v]
                timescale = np.arange(time_start, time_end, step)
                mini,maxi = np.inf, -np.inf
                xdata = np.append(line[i][0].get_xdata(), timescale)
                xdata = xdata[-self.max_window_len:]
                for idx in range(dat.shape[1]):
                    ydata = np.append(line[i][idx].get_ydata(), dat[:,idx])
                    ydata = ydata[-self.max_window_len:]
                    #print line[i][idx].get_xdata().shape, line[i][idx].get_ydata().shape, xdata.shape, ydata.shape, dat.shape
                    line[i][idx].set_xdata(xdata)
                    line[i][idx].set_ydata(ydata)
                    mini = min(mini,ydata.min())
                    maxi = max(maxi,ydata.max())
                ax[i].set_xlim(xmin=xdata.min(), xmax=xdata.max())
                ax[i].set_ylim(ymin=mini, ymax=maxi)
            fig.canvas.draw()

            self._thread_queue.task_done()

        # Empty the queue
        try:
            while True:
                self._thread_queue.get(False)
        except Queue.Empty:
            pass

    def __del__(self):
        self.stop_thread()

    def stop_thread(self):
        self._thread_cancelled = True
        self._thread_handle.join()
        self._thread_queue.join()

    def __call__(self, epoch, time_start_ms, time_end_ms, step_size_ms):
        #sys.stdout.write("Called!\n")
        #sys.stdout.flush()
        if not self._thread_cancelled:
            self._thread_queue.put((epoch, time_start_ms, time_end_ms, step_size_ms), True)
        return self.child(epoch, time_start_ms, time_end_ms, step_size_ms)

