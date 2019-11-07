# ----------------------------------------------------------------------------------------------------------------------
#
#      Class handling the training of any model
#
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
#
#           Imports and global variables
#       \**********************************/
#


# Basic libs
import tensorflow as tf
import numpy as np
import pickle
import os
from os import makedirs, remove
from os.path import exists, join
import time
import psutil
import sys

# PLY reader
from utils.ply import read_ply, write_ply

# Metrics
from utils.metrics import IoU_from_confusions, chamfer, earth_mover
from sklearn.metrics import confusion_matrix


# ----------------------------------------------------------------------------------------------------------------------
#
#           Trainer Class
#       \*******************/
#

class ModelTrainer:

    # Initiation methods
    # ------------------------------------------------------------------------------------------------------------------

    def __init__(self, model, restore_snap=None):

        # Add training ops
        self.add_train_ops(model)

        # Tensorflow Saver definition
        my_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='KernelPointNetwork')
        self.saver = tf.train.Saver(my_vars, max_to_keep=100)

        """
        print('*************************************')
        sum = 0
        for var in tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope='KernelPointNetwork'):
            #print(var.name, var.shape)
            sum += np.prod(var.shape)
        print('total parameters : ', sum)
        print('*************************************')

        print('*************************************')
        sum = 0
        for var in tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='KernelPointNetwork'):
            #print(var.name, var.shape)
            sum += np.prod(var.shape)
        print('total parameters : ', sum)
        print('*************************************')
        """

        # Create a session for running Ops on the Graph.
        # TODO: add auto check device
        on_CPU = False
        # on_CPU = True
        if on_CPU:
            cProto = tf.ConfigProto(device_count={'GPU': 0})
        else:
            cProto = tf.ConfigProto()
            cProto.gpu_options.allow_growth = True
        self.sess = tf.Session(config=cProto)

        # Init variables
        self.sess.run(tf.global_variables_initializer())

        # Name of the snapshot to restore to (None if you want to start from beginning)
        # restore_snap = join(self.saving_path, 'snapshots/snap-40000')
        if restore_snap is not None:
            # TODO: change this when new head & loss is fully integrated
            exclude_vars = ['softmax', 'head_unary_conv', '/fc/']
            restore_vars = my_vars
            for exclude_var in exclude_vars:
                restore_vars = [v for v in restore_vars if exclude_var not in v.name]
            restorer = tf.train.Saver(restore_vars)
            restorer.restore(self.sess, restore_snap)
            print("Model restored.")

    def add_train_ops(self, model):
        """
        Add training ops on top of the model
        """

        ##############
        # Training ops
        ##############

        with tf.variable_scope('optimizer'):

            # Learning rate as a Variable so we can modify it
            self.learning_rate = tf.Variable(model.config.learning_rate, trainable=False, name='learning_rate')

            # Create the gradient descent optimizer with the given learning rate.
            optimizer = tf.train.MomentumOptimizer(self.learning_rate, model.config.momentum)

            # Training step op
            gvs = optimizer.compute_gradients(model.loss)

            if model.config.grad_clip_norm > 0:

                # Get gradient for deformable convolutions and scale them
                scaled_gvs = []
                for grad, var in gvs:
                    if 'offset_conv' in var.name:
                        scaled_gvs.append((0.1 * grad, var))
                    if 'offset_mlp' in var.name:
                        scaled_gvs.append((0.1 * grad, var))
                    else:
                        scaled_gvs.append((grad, var))

                # Clipping each gradient independantly
                capped_gvs = [(tf.clip_by_norm(grad, model.config.grad_clip_norm), var) for grad, var in scaled_gvs]

                # Clipping the whole network gradient (problematic with big network where grad == inf)
                # capped_grads, global_norm = tf.clip_by_global_norm([grad for grad, var in gvs], self.config.grad_clip_norm)
                # vars = [var for grad, var in gvs]
                # capped_gvs = [(grad, var) for grad, var in zip(capped_grads, vars)]

                extra_update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
                with tf.control_dependencies(extra_update_ops):
                    self.train_op = optimizer.apply_gradients(capped_gvs)

            else:
                extra_update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
                with tf.control_dependencies(extra_update_ops):
                    self.train_op = optimizer.apply_gradients(gvs)

        ############
        # Result ops
        ############

        # Add the Op to compare the logits to the labels during evaluation.
        # TODO: change this after integrating pcn losses
        with tf.variable_scope('results'):

            gt_ds = tf.reshape(model.inputs['complete_points'], [-1, model.coarse.shape[1], 3])
            self.coarse_earth_mover = earth_mover(model.coarse, gt_ds)
            self.coarse_chamfer = chamfer(model.coarse, gt_ds)
            # TODO: dont have 2 sep losses fine & coarse, but one mixed with alpha...

        return

    # Training main method
    # ------------------------------------------------------------------------------------------------------------------

    def train(self, model, dataset, debug_NaN=False):
        """
        Train the model on a particular dataset.
        """

        if debug_NaN:
            # Add checking ops
            self.check_op = tf.add_check_numerics_ops()

        # Parameters log file
        if model.config.saving:
            model.parameters_log()

        # Save points of the kernel to file
        self.save_kernel_points(model, 0)

        if model.config.saving:
            # Training log file
            with open(join(model.saving_path, 'training.txt'), "w") as file:
                file.write('Steps out_loss reg_loss point_loss coarse_EM coarse_CD time memory\n')

            # Killing file (simply delete this file when you want to stop the training)
            if not exists(join(model.saving_path, 'running_PID.txt')):
                with open(join(model.saving_path, 'running_PID.txt'), "w") as file:
                    file.write('Launched with PyCharm')

        # Train loop variables
        t0 = time.time()
        self.training_step = 0
        self.training_epoch = 0
        mean_dt = np.zeros(2)
        last_display = t0
        self.training_preds = np.zeros(0)
        self.training_labels = np.zeros(0)
        epoch_n = 1
        mean_epoch_n = 0

        # Initialise iterator with train data
        self.sess.run(dataset.train_init_op)

        # Start loop
        while self.training_epoch < model.config.max_epoch:
            try:
                # Run one step of the model.
                t = [time.time()]
                ops = [self.train_op,
                       model.output_loss,
                       model.regularization_loss,
                       model.offsets_loss,
                       model.coarse,
                       model.complete_points,
                       self.coarse_earth_mover,
                       self.coarse_chamfer]

                # If NaN appears in a training, use this debug block
                if debug_NaN:
                    all_values = self.sess.run(ops + [self.check_op] + list(dataset.flat_inputs),
                                               {model.dropout_prob: 0.5})
                    L_out, L_reg, L_p, coarse, complete, coarse_em, coarse_cd = all_values[1:7]
                    if np.isnan(L_reg) or np.isnan(L_out):
                        input_values = all_values[8:]
                        self.debug_nan(model, input_values, coarse)
                        a = 1 / 0

                else:
                    # Run normal
                    _, L_out, L_reg, L_p, coarse, complete, coarse_em, coarse_cd = self.sess.run(ops, {
                        model.dropout_prob: 0.5})

                t += [time.time()]

                # Stack prediction for training confusion
                # if model.config.network_model == 'classification':
                #     self.training_preds = np.hstack((self.training_preds, np.argmax(probs, axis=1)))
                #     self.training_labels = np.hstack((self.training_labels, complete))
                t += [time.time()]

                # Average timing
                mean_dt = 0.95 * mean_dt + 0.05 * (np.array(t[1:]) - np.array(t[:-1]))

                # Console display (only one per second)
                if (t[-1] - last_display) > 1.0:
                    last_display = t[-1]
                    message = 'Step {:08d} L_out={:5.3f} L_reg={:5.3f} L_p={:5.3f} Coarse_EM={:4.2f} Coarse_CD={:4.2f} ' \
                              '---{:8.2f} ms/batch (Averaged)'
                    print(message.format(self.training_step,
                                         L_out,
                                         L_reg,
                                         L_p,
                                         coarse_em,
                                         coarse_cd,
                                         1000 * mean_dt[0],
                                         1000 * mean_dt[1]))

                # Log file
                if model.config.saving:
                    process = psutil.Process(os.getpid())
                    with open(join(model.saving_path, 'training.txt'), "a") as file:
                        message = '{:d} {:.3f} {:.3f} {:.3f} {:.2f} {:.2f} {:.2f} {:.1f}\n'
                        file.write(message.format(self.training_step,
                                                  L_out,
                                                  L_reg,
                                                  L_p,
                                                  coarse_em,
                                                  coarse_cd,
                                                  t[-1] - t0,
                                                  process.memory_info().rss * 1e-6))

                # Check kill signal (running_PID.txt deleted)
                if model.config.saving and not exists(join(model.saving_path, 'running_PID.txt')):
                    break

                if model.config.dataset.startswith('ShapeNetPart') or model.config.dataset.startswith('ModelNet'):
                    if model.config.epoch_steps and epoch_n > model.config.epoch_steps:
                        raise tf.errors.OutOfRangeError(None, None, '')

            except tf.errors.OutOfRangeError:

                # End of train dataset, update average of epoch steps
                mean_epoch_n += (epoch_n - mean_epoch_n) / (self.training_epoch + 1)
                epoch_n = 0
                self.int = int(np.floor(mean_epoch_n))
                model.config.epoch_steps = int(np.floor(mean_epoch_n))
                if model.config.saving:
                    model.parameters_log()

                # Snapshot
                if model.config.saving and (self.training_epoch + 1) % model.config.snapshot_gap == 0:

                    # Tensorflow snapshot
                    snapshot_directory = join(model.saving_path, 'snapshots')
                    if not exists(snapshot_directory):
                        makedirs(snapshot_directory)
                    self.saver.save(self.sess, snapshot_directory + '/snap', global_step=self.training_step + 1)

                    # Save points
                    self.save_kernel_points(model, self.training_epoch)

                # Update learning rate
                if self.training_epoch in model.config.lr_decays:
                    op = self.learning_rate.assign(tf.multiply(self.learning_rate,
                                                               model.config.lr_decays[self.training_epoch]))
                    self.sess.run(op)

                # Update hyper-parameter alpha
                if self.training_epoch in model.config.alpha_epoch:
                    op = model.alpha.assign(model.config.alphas[self.training_epoch])
                    self.sess.run(op)

                # Increment
                self.training_epoch += 1

                # Validation
                if model.config.network_model == 'classification':
                    self.validation_error(model, dataset)
                elif model.config.network_model == 'segmentation':
                    self.segment_validation_error(model, dataset)
                elif model.config.network_model == 'multi_segmentation':
                    self.multi_validation_error(model, dataset)
                elif model.config.network_model == 'cloud_segmentation':
                    self.cloud_validation_error(model, dataset)
                elif model.config.network_model == 'completion':
                    self.completion_validation_error(model, dataset)
                else:
                    raise ValueError('No validation method implemented for this network type')

                # self.training_preds = np.zeros(0)
                # self.training_labels = np.zeros(0)

                # Reset iterator on training data
                self.sess.run(dataset.train_init_op)

            except tf.errors.InvalidArgumentError as e:

                print('Caught a NaN error :')
                print(e.error_code)
                print(e.message)
                print(e.op)
                print(e.op.name)
                print([t.name for t in e.op.inputs])
                print([t.name for t in e.op.outputs])

                a = 1 / 0

            # Increment steps
            self.training_step += 1
            epoch_n += 1

        # Remove File for kill signal
        if exists(join(model.saving_path, 'running_PID.txt')):
            remove(join(model.saving_path, 'running_PID.txt'))
        self.sess.close()

    # Validation methods
    # ------------------------------------------------------------------------------------------------------------------

    def completion_validation_error(self, model, dataset):
        """
        Validation method for completion models
        """

        ##########
        # Initiate
        ##########

        # Initialise iterator with train data
        self.sess.run(dataset.val_init_op)

        # Initiate global prediction over all models
        # TODO: Unused?...
        if not hasattr(self, 'val_dist'):
            self.val_dist = np.zeros((len(dataset.complete_points['valid']), 2))
        # Choose validation smoothing parameter (0 for no smoothing, 0.99 for big smoothing)
        val_smooth = 0.95

        #####################
        # Network predictions
        #####################

        coarse_em_list = []
        coarse_cd_list = []
        # complete_points_list = []
        obj_inds = []

        mean_dt = np.zeros(2)
        last_display = time.time()
        while True:
            try:
                # Run one step of the model.
                t = [time.time()]
                ops = (self.coarse_earth_mover, self.coarse_chamfer, model.inputs['object_inds'])
                # TODO: dropout here is not used in the arch...check if this has side-effects
                coarse_em, coarse_cd, inds = self.sess.run(ops, {model.dropout_prob: 1.0})
                t += [time.time()]

                # Get distances and obj_indexes
                coarse_em_list += [coarse_em]
                coarse_cd_list += [coarse_cd]
                # complete_points_list += [complete_points]
                obj_inds += [inds]

                # Average timing
                t += [time.time()]
                mean_dt = 0.95 * mean_dt + 0.05 * (np.array(t[1:]) - np.array(t[:-1]))

                # Display
                if (t[-1] - last_display) > 1.0:
                    last_display = t[-1]
                    message = 'Validation : {:.1f}% (timings : {:4.2f} {:4.2f})'
                    print(message.format(100 * len(obj_inds) / model.config.validation_size,
                                         1000 * (mean_dt[0]),
                                         1000 * (mean_dt[1])))

            except tf.errors.OutOfRangeError:
                break

        coarse_em_mean = np.mean(coarse_em_list)
        coarse_cd_mean = np.mean(coarse_cd_list)
        print('Validation distances\nMean Chamfer: {:4.2f}\tMean Earth Mover: {:4.2f}'.format(coarse_cd_mean,
                                                                                              coarse_em_mean))

    # Saving methods
    # ------------------------------------------------------------------------------------------------------------------

    def save_kernel_points(self, model, epoch):
        """
        Method saving kernel point disposition and current model weights for later visualization
        """

        if model.config.saving:

            # Create a directory to save kernels of this epoch
            kernels_dir = join(model.saving_path, 'kernel_points', 'epoch{:d}'.format(epoch))
            if not exists(kernels_dir):
                makedirs(kernels_dir)

            # Get points
            all_kernel_points_tf = [v for v in tf.global_variables() if 'kernel_points' in v.name
                                    and v.name.startswith('KernelPoint')]
            all_kernel_points = self.sess.run(all_kernel_points_tf)

            # Get Extents
            if False and 'gaussian' in model.config.convolution_mode:
                all_kernel_params_tf = [v for v in tf.global_variables() if 'kernel_extents' in v.name
                                        and v.name.startswith('KernelPoint')]
                all_kernel_params = self.sess.run(all_kernel_params_tf)
            else:
                all_kernel_params = [None for p in all_kernel_points]

            # Save in ply file
            for kernel_points, kernel_extents, v in zip(all_kernel_points, all_kernel_params, all_kernel_points_tf):

                # Name of saving file
                ply_name = '_'.join(v.name[:-2].split('/')[1:-1]) + '.ply'
                ply_file = join(kernels_dir, ply_name)

                # Data to save
                if kernel_points.ndim > 2:
                    kernel_points = kernel_points[:, 0, :]
                if False and 'gaussian' in model.config.convolution_mode:
                    data = [kernel_points, kernel_extents]
                    keys = ['x', 'y', 'z', 'sigma']
                else:
                    data = kernel_points
                    keys = ['x', 'y', 'z']

                # Save
                write_ply(ply_file, data, keys)

            # Get Weights
            all_kernel_weights_tf = [v for v in tf.global_variables() if 'weights' in v.name
                                     and v.name.startswith('KernelPointNetwork')]
            all_kernel_weights = self.sess.run(all_kernel_weights_tf)

            # Save in numpy file
            for kernel_weights, v in zip(all_kernel_weights, all_kernel_weights_tf):
                np_name = '_'.join(v.name[:-2].split('/')[1:-1]) + '.npy'
                np_file = join(kernels_dir, np_name)
                np.save(np_file, kernel_weights)