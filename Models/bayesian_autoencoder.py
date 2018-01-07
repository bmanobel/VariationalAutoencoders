import tensorflow as tf
from tensorflow.examples.tutorials.mnist import input_data
import time

class BayesianAutoencoder(object):
    def __init__(self, n_datapoints, neurons_per_layer, mc_samples, batch_size, constant_prior=False):
        # SIZES
        self.N = n_datapoints
        self.layers = len(neurons_per_layer)
        self.neurons_per_layer = neurons_per_layer
        self.M = batch_size
        ## Set the number of Monte Carlo samples as a placeholder so that it can be different for training and test
        self.L =  tf.placeholder(tf.int32)
        
        self.constant_prior = constant_prior
        
        ## Batch data placeholders
        with tf.name_scope('input'):
            self.X = tf.placeholder(tf.float32, shape=[None, neurons_per_layer[0]], name='x-input')
            self.Y = tf.placeholder(tf.float32, shape=[None, neurons_per_layer[-1]], name='y-input')
            
        with tf.name_scope('input_reshape'):
            image_shaped_input = tf.reshape(self.X, [-1, 28, 28, 1])
            tf.summary.image('input', image_shaped_input, 10)
        
        # PRIOR OF WEIGHTS
        self.prior_mean_W, self.log_prior_var_W = self.get_prior_W()
    
        # POSTERIOR OF WEIGHTS
        self.mean_W, self.log_var_W = self.init_posterior_W()
        ## Builds whole computational graph with relevant quantities as part of the class
        self.loss, self.kl, self.ell, self.layer_out = self.get_nelbo()

        ## Initialize the session
        self.session = tf.Session()

    def get_prior_W(self):
        """
        Define a prior for the weight distribution.
        We assume them to be standard normal iid.
        """
        prior_mean_W = []
        log_prior_var_W = []
        
        for i in range(self.layers - 1):
            d_in = self.neurons_per_layer[i] + 1 # + 1 because of bias weight
            d_out = self.neurons_per_layer[i+1]
            
            with tf.name_scope("layer_" + str(i+1) + "_prior_weights"):
                if self.constant_prior:
                    prior_mean = tf.constant(0.0, shape=[d_in, d_out])
                    log_prior_var = tf.constant(0.0, shape=[d_in, d_out])
                else:
                    prior_mean = tf.Variable(tf.zeros([d_in, d_out]), name="p_W")
                    log_prior_var = tf.Variable(tf.zeros([d_in, d_out]), name="p_W")
                    
                tf.summary.histogram('prior_mean', tf.reshape(prior_mean, [-1]))
                tf.summary.histogram('prior_logvar', tf.reshape(log_prior_var, [-1]))
            
            prior_mean_W.append(prior_mean)
            log_prior_var_W.append(log_prior_var)
        
        return prior_mean_W, log_prior_var_W

    def init_posterior_W(self):
        """
        The (variational) posterior is assumed to be
        drawn from P mutually independent normal distributions.
        Hence, we have a diagonal covariance matrix and only need to store an array.
        """
        mean_W = []
        log_var_W = []
        
        for i in range(self.layers - 1):
            d_in = self.neurons_per_layer[i] + 1 # + 1 because of bias weight
            d_out = self.neurons_per_layer[i+1]

            with tf.name_scope("layer_" + str(i+1) + "_posterior_weights"):
                post_mean = tf.Variable(tf.zeros([d_in, d_out]), name="q_W")
                post_log_var = tf.Variable(tf.zeros([d_in, d_out]), name="q_W")
                tf.summary.histogram('posterior_mean', tf.reshape(post_mean, [-1]))
                tf.summary.histogram('posterior_logvar', tf.reshape(post_log_var, [-1]))
            
            mean_W.append(post_mean)
            log_var_W.append(post_log_var)
            
        return mean_W, log_var_W
    
    def get_std_norm_samples(self, d_in, d_out):
        """
        Draws N(0,1) samples of dimension [d_in, d_out].
        """
        return tf.random_normal(shape=[d_in, d_out])

    def sample_from_W(self):
        """
        Samples from the variational posterior approximation.
        We draw W-samples for each layer using the reparameterization trick.
        """

        for i in range(self.layers - 1):
            d_in = self.neurons_per_layer[i] + 1 # + 1 because of bias weight
            d_out = self.neurons_per_layer[i+1]
            z = self.get_std_norm_samples(d_in, d_out)
            ## division by 2 to obtain pure standard deviation
            w_from_q = tf.add(tf.multiply(z, tf.exp(self.log_var_W[i] / 2)), self.mean_W[i])
        
            yield w_from_q
    
    def feedforward(self, intermediate=0):
        """
        Feedforward pass excluding last layer's transfer function.
        intermediate : index of intermediate layer for output generation
        """
        
        # We will generate L output samples
        for i in range(self.L):
            
            outputs = self.X
            
            # Go through each layer (one weight matrix at a time)
            # and compute the (intermediate) output
            j = 0
            for weight_matrix in self.sample_from_W():
                outputs = tf.matmul(outputs, weight_matrix[1:,:]) + weight_matrix[0,:]
                tf.summary.histogram('activations', outputs)

                # if last layer is reached, do not use transfer function (softmax later on)
                if j == (self.layers - 2):
                    outputs = tf.sigmoid(outputs)
                else:
                    outputs = tf.nn.tanh(outputs)

                tf.summary.histogram('outputs', outputs)
                
                j += 1
                
                if j == intermediate:
                    break
                
            # use generator to save memory space
            yield outputs
    
    def predict(self, intermediate=0):
        """
        Predict using monte carlo sampling.
        """
        
        expected_output = 0
        
        for output in self.feedforward(intermediate):
            expected_output += output
            
        return expected_output / self.L
    
    def get_ell(self):
        """
        Returns the expected log-likelihood of the lower bound.
        For this we draw L samples from W, compute the log-likelihood for each
        and average the log-likelihoods in the end (expectation approximation).
        """
        
        log_p = 0
        cum_output = 0
        
        for output in self.feedforward():
            log_p_per_sample = tf.reduce_sum(tf.reduce_sum(
                                    self.Y * tf.log(output + 1e-10) + (1 - self.Y) * tf.log(1 - output + 1e-10),
                                    reduction_indices=[1]))
            log_p += log_p_per_sample
            cum_output += output
            
        ell = batch_log_p / self.L
        avg_output = cum_output / self.L
        
        return ell, avg_output

    def get_kl(self, mean_W, log_var_W, prior_mean_W, log_prior_var_W):
        """
        KL[q || p] returns the KL-divergence between the prior p and the variational posterior q.
        :param mq: vector of means for q
        :param log_vq: vector of log-variances for q
        :param mp: vector of means for p
        :param log_vp: vector of log-variances for p
        :return: KL divergence between q and p
        """
        mq = mean_W
        log_vq = log_var_W
        mp = prior_mean_W
        log_vp = log_prior_var_W
        
        return 0.5 * tf.reduce_sum(log_vp - log_vq + (tf.pow(mq - mp, 2) / tf.exp(log_vp)) + tf.exp(log_vq - log_vp) - 1)

    def get_kl_multi(self):
        """
        Compute KL divergence between variational and prior using a multi-layer-network
        """
        kl = 0
        
        for i in range(self.layers - 1):
            kl = kl + self.get_kl(
                        self.mean_W[i],
                        self.log_var_W[i],
                        self.prior_mean_W[i],
                        self.log_prior_var_W[i]
            )
        
        return kl
    
    def get_nelbo(self):
        """ Returns the negative ELBOW, which allows us to minimize instead of maximize. """
        # the kl does not change among samples
        kl = self.get_kl_multi()
        ell, layer_out = self.get_ell()
        # we take the mean instead of the sum to give it the same weight as for the KL-term
        batch_ell = tf.reduce_mean(ell)
        nelbo = kl - batch_ell # * self.N / float(self.M)
        return nelbo, kl, batch_ell, layer_out
    
    def learn(self, learning_rate=0.01, epochs=50):
        """ Our learning procedure """
        optimizer = tf.train.AdamOptimizer(learning_rate)

        ## Set all_variables to contain the complete set of TF variables to optimize
        all_variables = tf.trainable_variables()

        ## Define the optimizer
        train_step = optimizer.minimize(self.loss, var_list=all_variables)

        tf.summary.scalar('negative_elbo', self.loss)
        tf.summary.scalar('kl_div', self.kl)
        tf.summary.scalar('ell', self.ell)
        
        merged = tf.summary.merge_all()
        
        train_writer = tf.summary.FileWriter('logs/train', self.session.graph)
        test_writer = tf.summary.FileWriter('logs/test')        
        
        ## Initialize all variables
        init = tf.global_variables_initializer()

        ## Initialize TF session
        self.session.run(init)

        for i in range(epochs):
            start_time = time.time()
            print("Epoch: ", i)
            train_cost = 0
            cum_ell = 0
            cum_kl = 0
            
            old_progress = 0
            for batch_i in range(mnist.train.num_examples // self.M):
                progress = round(float(batch_i) / (mnist.train.num_examples // self.M) * 100)
                if progress % 10 == 0 and progress != old_progress:
                    print('Progress: ', str(progress) + '%')
                    old_progress = progress

                batch_xs, _ = mnist.train.next_batch(self.M)

                _, loss, ell, kl, summary = self.session.run(
                    [train_step, self.loss, self.ell, self.kl, merged],
                    feed_dict={self.X: batch_xs, self.Y: batch_xs, self.L: 1})
                train_writer.add_summary(summary, i)
                train_cost += loss
                cum_ell += ell
                cum_kl += kl
            

            print("NELBO: ", train_cost / (mnist.train.num_examples // self.M))
            print("ELL: ", -cum_ell / (mnist.train.num_examples // self.M))
            print("KL: ", cum_kl / (mnist.train.num_examples // self.M))
            print('Epoch training time: ', time.time() - start_time)
            
            val_cost = self.benchmark(validation=True)
            print('Validation cost: ', val_cost)
        
        
        train_writer.close()
        test_writer.close()
        
    def benchmark(self, validation=False):
        if validation:
            benchmark_data = mnist.validation
            label = 'Validation loss:'
        else:
            benchmark_data = mnist.test
            label = 'Test loss:'
        
        cost = 0
        for batch_i in range(benchmark_data.num_examples // self.M):
            batch_xs, _ = benchmark_data.next_batch(batch_size)
            cost += self.session.run(self.loss,
                                   feed_dict={self.X: batch_xs, self.Y: batch_xs, self.L: 1})
        return cost / (benchmark_data.num_examples // self.M)
        
    def serialize(self, path):
        saver = tf.train.Saver()
        save_path = saver.save(self.session, path)
        print("Model saved in file: %s" % save_path)
        
    def restore(self, path):
        saver = tf.train.Saver()   
        sess = tf.Session()
        saver.restore(sess, save_path=path)
        self.session = sess
    
    def predict(self, batch):
        outputs = self.layer_out
        return self.session.run(outputs, feed_dict={self.X: batch, self.Y: batch, self.L: 10})
    
    def get_weights(self):
        weights = (self.prior_mean_W, self.log_prior_var_W, self.mean_W, self.log_var_W)
        return self.session.run(weights)