import cv2
import pandas as pd
from data_files import FIGRURES_DIR, RESULT_DIR
import os 
from robobo_interface import (
    IRobobo,
    Emotion,
    LedId,
    LedColor,
    SoundEmotion,
    SimulationRobobo,
    HardwareRobobo,
)
import numpy as np
import pickle
import csv
import gymnasium as gym
from stable_baselines3 import DDPG
from stable_baselines3.common.noise import NormalActionNoise

class GymEnv(gym.Env):

    # We don't need metadata, but the superclass wants to see it
    metadata = {"render_modes": ["dummy"]}


    def __init__(self, render_mode=None, rob=IRobobo, max_steps=100):
        super(GymEnv, self).__init__()


        assert render_mode is None
        assert rob != False 
        self.rob = rob

        # Maximum number of steps per episode
        self.max_steps = max_steps

        # Define action space
        # NOTE: currently action space is a 1D-array from [0,1] which maps onto forward actions in ._move()
        self.action_space = gym.spaces.Box(low=0, high=1, shape=(1,), dtype=np.float32)

        # Define observation space 
        # NOTE: currently state space is 3 states: left, middle & right camera mask percentages 
        self.observation_space = gym.spaces.Box(low=0, high=1, shape=(3,), dtype=np.float32)

        # Initialise for logging
        self.log_irsdata = []
        self.log_rewards = []
        self.log_collision = []
        self.log_actions = []
        self.total_steps = 0
        self.total_timesteps_in_learn = 5000

        self.cum_reward = 0
        self.food_count = 0

        # --- LOGGING ---
        self.log_df = pd.DataFrame(columns=['episode reward', 'total food'])
        print(self.log_df)

        # # TODO - remove later 
        # with open(str(RESULT_DIR / 'log/cum_reward.txt'), 'w') as f:
        #     f.write(f'{self.cum_reward}\n')


    def _set_camera(self, horizontal_pan=180, vertical_tilt=67):
        print("Setting Camera: ")
        self.rob.set_phone_pan_blocking(horizontal_pan, 100) 
        self.rob.set_phone_tilt_blocking(vertical_tilt, 100) 
        self.rob.set_phone_pan(horizontal_pan, 100) 
        self.rob.set_phone_tilt(vertical_tilt, 100) 
        print("horizontal pan:", self.rob.read_phone_pan())
        print("vertical tilt:", self.rob.read_phone_tilt())
        

    def _process_front_camera(self, bgr_image, save_images=False):
        # Get the image from the front camera
        # Ensure that the image is retrieved correctly and is in BGR format initially
        if bgr_image is None:
            print("No image received from the camera.")
            return False 
        
        # Convert the BGR image to HSV format
        hsv_image = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2HSV)
        # Define HSV range for green color
        lower_green = np.array([15, 150, 200])
        upper_green = np.array([75, 255, 250]) # adjusted 
        # Threshold the HSV image to get only green colors
        mask = cv2.inRange(hsv_image, lower_green, upper_green)

        # visualizations
        if save_images:
            cv2.imwrite(str(FIGRURES_DIR / f'sim_camera_{self.step_count}.png'), bgr_image)
            cv2.imwrite(str(FIGRURES_DIR / f'sim_camera_mask_{self.step_count}.png'), mask)

        # Calculate the proportion of green in the image
        greenVal = float(np.sum(mask > 0)) / float(mask.size)
        # Determine if green is detected
        if greenVal > 0.01:  # Adjust threshold as needed
            green = 1
        else:
            green = 0

        return green 
        # return greenVal 
    

    def _normalize_irs(self, irs) -> np.array:
        clipped_arr = np.clip(irs, 0, 1400)
        normalized_arr = clipped_arr / 1400.0
        return normalized_arr


    def _move(self, action):
        '''
        action is a 1D-array in range [0,1]
            low action -> turn right 
            high action -> turn left 
        '''
        v_min=0 
        v_max=100
        # Calculate the wheel speeds
        left_speed = v_min + (v_max - v_min) * (1 - action)
        right_speed = v_min + (v_max - v_min) * action
        self.rob.move_blocking(left_speed[0], right_speed[0], 100)

        
    def _get_obs(self):
        # -- irs component -- 
        obs_irs = self.rob.read_irs()
        obs_irs = self._normalize_irs(obs_irs)

        # -- camera component -- 
        bgr_image = self.rob.get_image_front()

        # make 3 vertical image sections
        width = bgr_image.shape[1] // 3
        left_image, middle_image, right_image  = bgr_image[:, :width, :], bgr_image[:, width:2*width, :], bgr_image[:, 2*width:, :]

        left = self._process_front_camera(left_image)
        middle = self._process_front_camera(middle_image)
        right = self._process_front_camera(right_image)
 
        # visualization 
        # _, _ = self._process_front_camera(bgr_image, save_images=True)

        # returns presence or percentage of pixels covered by the green mask 
        obs_camera = np.array([left, middle, right])

        return obs_irs, obs_camera 


    def _get_info(self):
        return {'dummy_info': 0}


    def _spin_at_episode_start(self):
        random_amount = np.random.randint(0, 1001)
        self.rob.move_blocking(100, -100, random_amount)
    

    def _get_reward(self, observation, action): 

        print('observation:', observation)
        print('action:', action)

        l, m, r = observation

        reward = 0 

        # if object detected only on left side
        if l == 1 and not m == 1: 
            # if robot turns left 
            if action > 0.5: 
                reward += 1 

        # if object detected in middle 
        elif m == 1:
            # if robot stays relatively straight 
            if 0.45 < action < 0.55:
                reward += 10
        
        # if object detected only on right side
        elif r == 1 and not m == 1:
            # if robot turns right 
            if action < 0.5: 
                reward += 1

        else: 
            # promote spinning to detect objects? -> NOTE: seems to do that on it's own 
            pass 

        # if food is collected 
        if self.rob.nr_food_collected() > self.food_count: 
            reward += 50
            print('found food!')

        return reward
    

    def reset(self, seed=None, options=None):
        # This line is probably needed but does nothing
        super().reset(seed=seed)

        if isinstance(self.rob, SimulationRobobo):
            if self.rob.is_running():
                self.rob.stop_simulation()
            
            self.rob.play_simulation()
        else:
            # self.rob.talk("episode starts in five")
            # self.rob.sleep(5)
            pass

        # set camera position at the start 
        self._set_camera(horizontal_pan=180, vertical_tilt=90)
        obs_irs, obs_camera = self._get_obs()
        observation = obs_camera

        info = self._get_info()

        # --- LOGGING --- 

        episode_logs = {
            'episode reward': [self.cum_reward],
            'total food': [self.food_count]
        }
        new_row = pd.DataFrame(episode_logs)

        self.log_df = pd.concat([self.log_df, new_row], ignore_index=True)
        
        if not os.path.exists(os.path.join(RESULT_DIR, 'log')):
            os.makedirs(os.path.join(RESULT_DIR, 'log'))
        # save at each episode 
        self.log_df.to_csv(str(RESULT_DIR / 'log/episode_logs.csv'), index=False)

        # re-initialize 
        self.step_count = 0
        self.cum_reward = 0 

        return observation, info


    def step(self, action):

        info = self._get_info()

        # Take the action
        self._move(action)
        # get state information 
        obs_irs, obs_camera = self._get_obs()

        # observation is the bool states of each camera green mask: left, middle, right 
        observation = obs_camera

        reward = self._get_reward(observation, action)
            
        print('observation:', observation)
        print('action:', action)
        print('reward:', reward)

        # tracking metrics metrics 
        self.total_steps += 1 
        self.cum_reward += reward
        self.food_count = self.rob.nr_food_collected()
        self.step_count += 1        

        # Determine if the episode is terminated based on the number of steps
        terminated = self.step_count >= self.max_steps

        # --- LOGGING ---

        # Output index 3 has to be False, because it is a deprecated feature
        return observation, reward, terminated, False, info


def task1(rob: IRobobo):

    model = DDPG(
        policy = 'MlpPolicy', 
        env = GymEnv(rob=rob, max_steps=100), 
        learning_rate=0.0001, 
        buffer_size=50000, 
        learning_starts=100, 
        batch_size=64, 
        tau=0.005, 
        gamma=0.99, 
        train_freq=1, 
        gradient_steps=1, 
        # action_noise=NormalActionNoise(mean=np.zeros(1), sigma=0.1 * np.ones(1)), # Change this too if the action space changes shape
        # action_noise=NormalActionNoise(mean=np.zeros(2), sigma=0.1 * np.ones(2)), # Change this too if the action space changes shape
        replay_buffer_class=None, 
        replay_buffer_kwargs=None, 
        optimize_memory_usage=False, 
        tensorboard_log=None, 
        policy_kwargs=None, 
        verbose=0, 
        seed=None, 
        device='auto', 
        _init_setup_model=True
    )

    model.learn(total_timesteps=5000, log_interval=10, progress_bar=True)
    
    model.save(str(RESULT_DIR / 'models/run_1'))


def validate_task1(rob: IRobobo):
    model = DDPG.load(str(RESULT_DIR / 'models/run_2c')) 

    env = GymEnv()
    obs = env.reset()[0]
    while True:
        action, states = model.predict(obs)
        observation, reward, terminated, _, info = env.step(action)


def task2(rob: IRobobo, model_name=None):
    if model_name is None:
        raise ValueError("model_name must be provided")

    model = DDPG(
        policy = 'MlpPolicy', 
        env = GymEnv(rob=rob, max_steps=100), 
        learning_rate=0.0001, 
        buffer_size=50000, 
        learning_starts=100, 
        batch_size=64, 
        tau=0.005, 
        gamma=0.99, 
        train_freq=1, 
        gradient_steps=1, 
        # action_noise=NormalActionNoise(mean=np.zeros(1), sigma=0.1 * np.ones(1)), # Change this too if the action space changes shape
        # action_noise=NormalActionNoise(mean=np.zeros(2), sigma=0.1 * np.ones(2)), # Change this too if the action space changes shape
        replay_buffer_class=None, 
        replay_buffer_kwargs=None, 
        optimize_memory_usage=False, 
        tensorboard_log=None, 
        policy_kwargs=None, 
        verbose=0, 
        seed=None, 
        device='auto', 
        _init_setup_model=True
    )

    model.learn(total_timesteps=5000, log_interval=10, progress_bar=True) # change back to 5000
    
    model.save(str(RESULT_DIR / f'models/{model_name}'))


def task2_demonstrate(rob: IRobobo, steps=100000, model_name=None):
    if model_name is None:
        raise ValueError("model_name must be provided")

    model = DDPG.load(str(RESULT_DIR / f'models/{model_name}')) 
    env = GymEnv(rob=rob, max_steps=1000)
    obs = env.reset()[0]

    for _ in range(steps):
        action, states = model.predict(obs)
        observation, reward, terminated, _, info = env.step(action)


def calibrate(rob: IRobobo):
    # Start the simulation
    if isinstance(rob, SimulationRobobo):
        rob.play_simulation()
        type = 'simulation'
    else: 
        type = 'hardware'


    # logging 
    positions = []
    irs_data = []

    # If sensor sees something: turn right, if not: straight ahead
    for _ in range(100):

        # Somehow the first value for read_irs() in the simulation is always [inf, inf, ...]
        # so the first value is hard_set to 0
        if _ == 0:
            irs = [0,0,0,0,0]
        else:
            # Read ['FrontL', 'FrontR', 'FrontC', 'FrontRR', 'FrontLL']
            irs = rob.read_irs()[2:6] + rob.read_irs()[7:8]
        
        print(irs)
        irs_data.append(irs)

        if isinstance(rob, SimulationRobobo):
            pos = rob.get_position()
            print(pos)
            positions.append(pos)
        # move back 
        rob.move_blocking(-10, -10, 500)

    # Stop simulation
    if isinstance(rob, SimulationRobobo):
        rob.stop_simulation()

    # logging 
    run = 2
    df = pd.DataFrame(irs_data, columns=['FrontL', 'FrontR', 'FrontC', 'FrontRR', 'FrontLL'])
    # df.to_csv(str(RESULT_DIR / f'{type}_irs_calibrate_{run}.csv'), index=False)

    # with open(str(RESULT_DIR / f'{type}_pos_calibrate_{run}.csv'), 'w') as file:
    #     for item in positions:
    #         file.write(f"{item}\n")
