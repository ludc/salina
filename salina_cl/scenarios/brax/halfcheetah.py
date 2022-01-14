from salina_cl.core import RLTask
from salina_cl.core import Scenario
from brax.envs import wrappers
import brax
from brax.envs.halfcheetah import Halfcheetah
from google.protobuf import text_format
from brax.envs.halfcheetah import _SYSTEM_CONFIG as halfcheetah_config

env_cfgs = {
    "normal":{},
    "disproportionate_feet":{
      "torso": 0.75,
      "thigh": 0.75,
      "shin": 0.75,
      "foot": 1.25
      },
    "modified_physics":{
      "gravity": 1.5,
      "friction": 1.25,
      }
}


class CustomHalfcheetah(Halfcheetah):
    def __init__(self, env_cfg, **kwargs):
        config = text_format.Parse(halfcheetah_config, brax.Config())
        env_specs = env_cfgs[env_cfg]
        for spec,coeff in env_specs.items():
            if spec == "gravity":
                config.gravity.z *= coeff
            elif spec == "friction":
                config.friction *= coeff
            else:
                for body in config.bodies:
                    if spec in body.name:
                        body.mass *= coeff
                        body.colliders[0].capsule.radius *= coeff
        self.sys = brax.System(config)


def make_halfcheetah(seed = 0,
                   batch_size = None,
                   max_episode_steps = 1000,
                   action_repeat = 1,
                   backend = None,
                   auto_reset = True,
                   env_cfg = "normal",
                   **kwargs):

    env = CustomHalfcheetah(env_cfg, **kwargs)
    if max_episode_steps is not None:
        env = wrappers.EpisodeWrapper(env, max_episode_steps, action_repeat)
    if batch_size:
        env = wrappers.VectorWrapper(env, batch_size)
    if auto_reset:
        env = wrappers.AutoResetWrapper(env)
    if batch_size is None:
        return wrappers.GymWrapper(env, seed=seed, backend=backend)
    return wrappers.VectorGymWrapper(env, seed=seed, backend=backend)

class MultiHalfcheetah(Scenario):
    def __init__(self,n_train_tasks,n_train_envs,n_evaluation_envs,max_episode_steps):
        env = make_halfcheetah(10)
        input_dimension = [env.observation_space.shape[0]]
        output_dimension = [env.action_space.shape[0]]

        cfgs = list(env_cfgs.keys())
        self._train_tasks=[]
        for k in range(n_train_tasks):
            agent_cfg={
                "classname":"cl.rl.scenarios.brax.AutoResetBraxAgent",
                "make_env_fn":make_halfcheetah,
                "make_env_args":{
                                "max_episode_steps":max_episode_steps,
                                 "env_cfg":cfgs[k % len(cfgs)]},
                "n_envs":n_train_envs
            }
            self._train_tasks.append(RLTask(agent_cfg,input_dimension,output_dimension,k))

        self._test_tasks=[]
        for k in range(n_train_tasks):
            agent_cfg={
                "classname":"cl.rl.scenarios.brax.AutoResetBraxAgent",
                "make_env_fn":make_halfcheetah,
                "make_env_args":{"max_episode_steps":max_episode_steps,
                                 "env_cfg":cfgs[k % len(cfgs)]},
                "n_envs":n_evaluation_envs
            }
            self._test_tasks.append(RLTask(agent_cfg,input_dimension,output_dimension,k))

    def train_tasks(self):
        return self._train_tasks

    def test_tasks(self):
        return self._test_tasks