"""


"""
from fractions import gcd
import operator
import Queue
import warnings
import numpy as np
import sys
import os

# todo: doctest
# todo: observer callback? other callbacks?
# todo: supervisor revert name (string)
# todo: action name might be interesting (e.g. when observing gaits)

class WebotsRobotMixin(object):
    """Webots Robot controller. It samples all sensors and periodically
    consults an ``actor`` for control decisions.
    
    ``actor``
        A function which determines the motor targets for the next
        control period. See *RobotActor* for specifics.
        
        The function must return an interator which is valid for at
        least *ctrl_period_ms* / *motor_period_ms* steps. In each step,
        it must return a list of four motor targets.
        
        The actor's interface is defined by :py:class:`RobotActor`. Note
        however, that the interface is organized such that the class
        structure may be obsolete.
        
    ``sampling_period_ms``
        The period according to which sensors are sampled.
        In milliseconds.
    
    ``ctrl_period_ms``
        The period of control actions. In milliseconds.
        Must be a larger than or equal to the motor period and, if
        larger, a multiple thereof.
    
    ``motor_period_ms``
        The period according to which motor targets are set. In
        milliseconds. Usually, the same as *sampling_period_ms*
        (the default). If not, it's advised that it's a multiple of the
        sampling period, otherwise the observations per control decision
        may become funny.
    
    ``event_period_ms``
        The period in milliseconds that is used for polling the
        receiver. Should optimally be a multiple of the control or
        sampling period or the Supervisor's sampling period.
    
    ``noise_ctrl``
        Additive zero-mean gaussian noise on the motor targets.
        Additional to whatever *webots* does. Either a scalar of 4-tuple
        is expected, which represents the noise variances for all of the
        motors or each individual one, respectively. Use None to discard
        the noise (default).
    
    ``noise_obs``
        Additive zero-mean gaussian noise on the motor targets.
        Additional to whatever *webots* does. Either a scalar of dict
        is expected, which represents the noise variances for all of the
        sensors or each individual one, respectively. In the latter
        approach, the dict keys have to correspond to the sensor name.
        Use None to discard the noise (default).
    
    """
    def __init__(self, actor, sampling_period_ms=20, ctrl_period_ms=2000, motor_period_ms=None, event_period_ms=None, noise_ctrl=None, noise_obs=None):
        # action
        self.actor = actor
        
        # init time constants
        self.sampling_period = sampling_period_ms
        self.ctrl_period = ctrl_period_ms
        if motor_period_ms is None:
            motor_period_ms = sampling_period_ms
        if event_period_ms is None:
            event_period_ms = sampling_period_ms
        self.motor_period = motor_period_ms
        self.event_period = event_period_ms
        
        # check assumptions on periods
        assert self.ctrl_period >= self.motor_period
        assert self.ctrl_period % self.motor_period == 0
        if self.motor_period > self.sampling_period:
            if self.motor_period % self.sampling_period != 0:
                warnings.warn('The motor period is not a multiple of the sampling period')
        elif self.motor_period < self.sampling_period:
            if self.sampling_period % self.motor_period != 0:
                warnings.warn('The sampling period is not a multiple of the motor period')
        
        # init noise
        self.motor_noise = noise_ctrl
        self.observer_noise = noise_obs
        
        # init sensors and motors
        self._motors = []
        self._sensors_1d = {}
        self._sensors_3d = {}
        
        # init events
        self._events = {}
        self._emitters = {}
    
    def run(self):
        """Main controller loop. Runs infinitely unless aborted by
        webots.
        
        The controller operates on a sense, think, act cycle. In every
        step - according to the sampling period - the sensors are read
        out (sense). This information is passed to the control decision
        machine (think) and its result used for updating the motors
        (act).
        
        The ``actor`` is provided with all sensor readings, starting
        from the previous call up to the current one. The very last
        readout is equal to the current state of the robot.
        The motor targets (denoted by trg) are the ones that have been
        applied in the previous step. This can be interpreted as the
        target that caused the sensor readings. Also note, that the
        motor targets returned by the ``actor`` will be effective
        immediately, i.e. the first target will be set right after the
        ``actor`` was called.
        
        .. versionchanged:: 1365
            After version 1365 (10eb3eed-6697-4d8c-9aac-32ebf1d36239),
            the behaviour of the main loop was changed: There's an
            initialization of the motor target and the target executed
            before the measurement (so long, it was executed after). We
            need to discuss and test this to be more specific about the
            behaviour.
        
        """
        # init sensor readouts
        sensor_labels, read_sensors = self.get_readout()
        sensor_labels = self.motor_names() + sensor_labels
        
        # init time
        sys.stdout.flush()
        current_time = 0
        # gcd is associative and commutative
        loop_wait = gcd(self.motor_period, self.sampling_period)
        loop_wait = gcd(self.event_period, loop_wait)
        epoch = Queue.deque(maxlen=self.ctrl_period/self.sampling_period)
        
        # get initial target from actor
        motor_targets = self.actor._get_initial_targets(current_time, current_time + self.ctrl_period, self.motor_period)
        current_target = motor_targets.next()
        self._set_targets(current_target)
        
        # main loop
        while True:
            # advance
            if self.step(loop_wait) == -1:
                break
            
            current_time += loop_wait
            
            # first serve receiver callback
            if current_time % self.event_period == 0:
                # check messages in receiver
                for receiver in self._events:
                    while receiver.getQueueLength() > 0:
                        msg = receiver.getData()
                        # use the RobotActor way of event-handling:
                        if hasattr(self.actor, "signal") and callable(self.actor.signal):
                            self.actor.signal(msg, current_time=current_time, epoch=epoch)
                        
                        # use additional optional event handlers:
                        for handler in self._events[receiver]:
                            handler(self, epoch, current_time, msg)
                        receiver.nextPacket()
            
            # sense
            # update observations
            if current_time % self.sampling_period == 0:
                sensors_current = read_sensors.next()
                epoch.append( current_target + sensors_current )
            
            # think
            # next action
            if current_time % self.ctrl_period == 0:
                sensor_epoch = dict(zip(sensor_labels, map(np.array, zip(*epoch))))
                if self.observer_noise is not None:
                    self._add_observer_noise(sensor_epoch)
                
                motor_targets = self.actor(sensor_epoch, current_time, current_time + self.ctrl_period, self.motor_period)
                epoch.clear()
            
            # act
            # set motor target
            if current_time % self.motor_period == 0:
                current_target = motor_targets.next()
                if self.motor_noise is not None:
                    current_target = self._add_motor_noise(current_target)
                self._set_targets(current_target)
        
        # teardown
        self._post_run_hook(current_time)
        return self
    
    def send_msg(self, msg, emitter_name=None):
        """Send a message ``msg`` through a device ``emitter_name``. If
        the ``emitter_name`` is :py:const:`None`, the message will
        be sent through all available devices."""
        if emitter_name is None:
            for emitter in self._emitters.values():
                emitter.send(msg)
        else:
            self._emitters[emitter_name].send(msg)
    
    def add_emitter(self, name):
        """Add an emitter called ``name``."""
        if name in self._emitters:
            return
        
        self._emitters[name] = self.getEmitter(name)
    
    def motor_names(self):
        """Return the motor's names."""
        return map(lambda (name, dev): name, self._motors)
    
    def _post_run_hook(self, current_time_ms):
        """Cleanup after the main loop has terminated.
        
        The main loop may exit (e.g. if the simulation is reverted) and
        the robot instance closed in the process. Since the destructor
        is not called by default, this hook provides a method to clean
        up any remaining resources and execute post-run code.
        
        The default action is to delete the actor, thus envoking its
        destructor (if any).
        
        ``current_time``
            Time when the main loop was interrupted, in milliseconds.
            
        """
        del self.actor
    
    def _add_observer_noise(self, epoch):
        """Add observer noise, according to *observer_noise*.
        """
        motor_names = self.motor_names()
        if isinstance(self.observer_noise, dict):
            # dict case, individual noise per sensor
            for k in self.observer_noise: # len(obs_noise) <= len(epoch)
                if k in epoch and k not in motor_names:
                    epoch[k] += np.random.normal(scale=self.observer_noise[k], size=epoch[k].shape)
        else:
            # scalar case, same noise for all sensors
            for k in epoch:
                if k in motor_names:
                    continue
                epoch[k] += np.random.normal(scale=self.observer_noise, size=epoch[k].shape)
        return epoch
    
    def _add_motor_noise(self, current_target):
        """Add motor noise, according to *motor_noise*.
        """
        if isinstance(self.motor_noise, int) or isinstance(self.motor_noise, float):
            # scalar case, same noise for all motors
            noise = np.random.normal(scale=self.motor_noise, size=(len(self._motors),))
        else:
            # list case, individual noise per motor
            noise = [np.random.normal(scale=sig) for sig in self.motor_noise]
        current_target = map(operator.add, current_target, noise)
        return current_target
    
    def _set_targets(self, current_target):
        """Abstract function for setting the actuator targets.
        """
        raise NotImplementedError()
    
    def add_receiver(self, receiver_name, callback=None):
        """Add a ``receiver_name`` for polling. If a new message is
        available ``callback`` is to be called. If ``callback`` is a
        list, all its items will be called on the same message.
        """
        receiver = self.getReceiver(receiver_name)
        receiver.enable(self.event_period)
        
        if receiver not in self._events:
            self._events[receiver] = []
        
        if callback is not None:
            if callable(callback):
                self._events[receiver].append(callback)
            else:
                # Assuming list type
                self._events[receiver].extend(list(callback))
        
        return self
    
    def add_sensor(self, name, clbk, dim=1):
        """Add a sensor ``name`` to the robot. The function ``clbk``
        reads out sensor value(s). Each readout must either produce
        one (``dim`` = 1) or three (``dim`` = 3) readout values.
        """
        if dim == 1:
            self._sensors_1d[name] = clbk
        elif dim == 3:
            self._sensors_3d[name] = clbk
        else:
            raise NotImplementedError()
        
        return self
    
    def add_motor(self, name, device):
        """Add a motor ``name`` to the robot with the corresponding
        webots node ``device``.
        """
        self._motors.append((name, device))
        return self
    
    def get_readout(self):
        """Return labels and a generator for reading out sensor values.
        The labels and values returned by the generator have the same
        order.
        """
        gen = self._readout_gen()
        lbl = self._readout_labels()
        return lbl, gen
    
    def _readout_gen(self):
        """Produce a sensor readout generator."""
        sensors_1d = self._sensors_1d.values()
        sensors_3d = self._sensors_3d.values()
        while True:
            single = map(lambda f: f(), sensors_1d)
            three = map(lambda (x, z, y):[x, y, z], map(lambda f:f(), sensors_3d))
            yield single + reduce(operator.add, three)
    
    def _readout_labels(self):
        """Return a list of sensor labels where the order is the same
        as in the sensor readouts."""
        single = self._sensors_1d.keys()
        three = reduce(operator.add, map(lambda i: [i+'_x', i+'_y', i+'_z'], self._sensors_3d.keys()))
        return single + three

class WebotsPuppyMixin(WebotsRobotMixin):
    """The actual Puppy Robot implementation.
    
    ``event_handlers``
        List of receiver callbacks. Only allowed as keyword argument.
        The callbacks must implement the
        :py:func:`event_handler_template` interface.
    
    """
    def __init__(self, *args, **kwargs):
        event_handlers = kwargs.pop('event_handlers', [])
        super(WebotsPuppyMixin, self).__init__(*args, **kwargs)
        
        # Sensor names
        _s_accel = 'accelerometer'
        _s_gyro = 'gyro'
        _s_compass = 'compass'
        _s_gps = 'puppyGPS'
        #_s_cam = 'camera'
        
        _s_target = ('trg0', 'trg1', 'trg2', 'trg3')
        _s_hip = ('hip0', 'hip1', 'hip2', 'hip3')
        _s_knee = ('knee0', 'knee1', 'knee2', 'knee3')
        _s_touch = ('touch0', 'touch1', 'touch2', 'touch3')
        
        # sensor vars
        acc = self.getAccelerometer(_s_accel)
        gyr = self.getGyro(_s_gyro)
        mgn = self.getCompass(_s_compass)
        gps = self.getGPS(_s_gps)
        servos = [self.getServo(s) for s in _s_hip + _s_knee]
        touch = [self.getTouchSensor(t) for t in _s_touch]
        #cam = self.getCamera(_s_cam)
        
        # enable sensors
        acc.enable(self.sampling_period)
        gyr.enable(self.sampling_period)
        mgn.enable(self.sampling_period)
        gps.enable(self.sampling_period)
        for sensor in servos:
            sensor.enablePosition(self.sampling_period)
        for sensor in touch:
            sensor.enable(self.sampling_period)
        #cam.enable(self.sampling_period)
        
        # register sensors
        self.add_sensor(_s_accel, acc.getValues, dim=3)
        self.add_sensor(_s_gyro, gyr.getValues, dim=3)
        self.add_sensor(_s_compass, mgn.getValues, dim=3)
        self.add_sensor(_s_gps, gps.getValues, dim=3)
        for name, sensor in zip(_s_hip + _s_knee, servos):
            self.add_sensor(name, sensor.getPosition)
        for name, sensor in zip(_s_touch, touch):
            self.add_sensor(name, sensor.getValue)
        
        # register motors
        for name, motor in zip(_s_target, servos[:4]):
            self.add_motor(name, motor)
        
        # register receiver
        self.add_receiver('fromSupervisorReceiver', event_handlers)
        self.add_emitter('toSupervisorEmitter')
    
    def _set_targets(self, current_target):
        """Set the targets."""
        for trg, (name, motor) in zip(current_target, self._motors):
            motor.setPosition(trg)

class WebotsSupervisorMixin(object):
    """Webots supervisor 'controller'. It actively probes the simulation,
    performs checks and reverts the simulation if necessary.
    
    ``sampling_period_ms``
        The period in milliseconds which the supervisor uses to observe
        the robot and possibly executes actions.
    
    ``checks``
        A list of callables, which are executed in order in every
        sampling step. A check's interface must be compliant with
        the one of :py:class:`SupervisorCheck`.
    
    """
    def __init__(self, sampling_period_ms, checks=[]):
        self.checks = checks
        self.loop_wait = sampling_period_ms
        self.emitter = self.getEmitter('toRobotEmitter')
        self.receiver = self.getReceiver('fromRobotReceiver')
        self.receiver.enable(self.loop_wait)
        self.num_iter = 0
        
    def run(self):
        """Supervisor's main loop. Call this function upon script
        initialization, such that the supervisor becomes active.
        
        The routine runs its checks and reverts the simulation if
        indicated. The iterations counter is made available to the
        checks through *WebotsSupervisorMixin.num_iter*.
        
        Note, that reverting the simulation restarts the whole simulation
        which also reloads the supervisor.
        
        """
        self.num_iter = 0
        while True:
            
            # run checks
            for check in self.checks:
                check(self)
            
            # advance
            if self.step(self.loop_wait) == -1:
                break
            self.num_iter += self.loop_wait
        
        self._post_run_hook()
        return self
    
    def _pre_revert_hook(self, reason):
        """A custom function (default empty) that is executed just
        before the simulation is reverted.
        
        ``reason``
            A string identifying the check which requested the revert.
        
        """
        pass
    
    def _post_run_hook(self):
        """Cleanup after the main loop has terminated.
        
        The main loop may exit (e.g. if the simulation is reverted) and
        the supervisor instance closed in the process. Since the
        destructor is not called by default, this hook provides a
        method to clean up any remaining resources and execute
        post-run code.
        
        """
        pass

class RecordingSupervisor(WebotsSupervisorMixin):
    """Record the simulation into a webots animation file stored at
    ``anim_filename``. The file extension should be 'wva'.
    
    .. todo::
        Webots (PRO 6.4.4) crashes when the simulation is stopped
    
    """
    def __init__(self, anim_filename, *args, **kwargs):
        super(RecordingSupervisor, self).__init__(*args, **kwargs)
        self.startAnimation(anim_filename)
        
    def _post_run_hook(self):
        """Stop the animation.
        """
        self.stopAnimation()

def event_handler_template(robot, epoch, current_time, msg):
    """Template function for event_handler function of
    :py:class:`WebotsRobotMixin:`.
    
    ``robot``
        Instance of :py:class:`WebotsRobotMixin` with the current robot.
    
    ``epoch``
        :py:keyword:`dict` containing the current sensor readings.
        
    
    ``current_time``
        :py:func:`int` with the current time step in ms.
    
    ``msg``
        :py:func:`str` containing the event message.
    """
    pass

def mixin(cls_mixin, cls_base):
    """Create a class which derives from two base classes.
    
    The intention of this function is to set up a working *webots*
    controller, using webots's Robot class and the Puppy mixin from
    this library.
    
    ``mixin``
        The mixin class, e.g.
        *WebotsPuppyMixin* or *WebotsSupervisorMixin*
    
    ``base``
        The base class, e.g.
        *Robot* or *Supervisor*
    
    """
    class WebotsMixin(cls_mixin, cls_base):
        """Trivial class to correctly set up the webots mixin."""
        def __init__(self, *args, **kwargs):
            cls_base.__init__(self)
            cls_mixin.__init__(self, *args, **kwargs)
    
    return WebotsMixin

def builder(cls_mixin, cls_base, *args, **kwargs):
    """Set up a mixin class and return an instance of it.
    """
    cls = mixin(cls_mixin, cls_base)
    return cls(*args, **kwargs)

def robotBuilder(cls_base, *args, **kwargs):
    """Return an instance of a webots puppy robot.
    """
    return builder(WebotsPuppyMixin, cls_base, *args, **kwargs)

def supervisorBuilder(cls_base, *args, **kwargs):
    """Return an instance of a webots puppy supervisor.
    """
    return builder(WebotsSupervisorMixin, cls_base, *args, **kwargs)

