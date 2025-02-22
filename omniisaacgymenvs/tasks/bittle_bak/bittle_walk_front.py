# Copyright (c) 2018-2022, NVIDIA Corporation
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from
#    this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
from omniisaacgymenvs.tasks.base.rl_task import RLTask
from omniisaacgymenvs.robots.articulations.bittle import Bittle
from omniisaacgymenvs.robots.articulations.views.bittle_view import BittleView
from omniisaacgymenvs.tasks.utils.usd_utils import set_drive

from omni.isaac.core.utils.prims import get_prim_at_path

from omni.isaac.core.utils.torch.rotations import *

import numpy as np
import torch
import math
import time


class BittleTask(RLTask):
    def __init__(
            self,
            name,
            sim_config,
            env,
            offset=None
    ) -> None:
        self._sim_config = sim_config
        self._cfg = sim_config.config
        self._task_cfg = sim_config.task_config
        print("self._task_cfg:", self._task_cfg)
        print("self._cfg:", self._cfg)

        # normalization
        self.lin_vel_scale = self._task_cfg["env"]["learn"]["linearVelocityScale"]
        self.ang_vel_scale = self._task_cfg["env"]["learn"]["angularVelocityScale"]
        self.dof_pos_scale = self._task_cfg["env"]["learn"]["dofPositionScale"]
        self.dof_vel_scale = self._task_cfg["env"]["learn"]["dofVelocityScale"]
        self.action_scale = self._task_cfg["env"]["control"]["actionScale"]

        # reward scales
        self.rew_scales = {}
        self.rew_scales["lin_vel_xy"] = self._task_cfg["env"]["learn"]["linearVelocityXYRewardScale"]
        self.rew_scales["ang_vel_z"] = self._task_cfg["env"]["learn"]["angularVelocityZRewardScale"]
        self.rew_scales["lin_vel_z"] = self._task_cfg["env"]["learn"]["linearVelocityZRewardScale"]
        self.rew_scales["joint_acc"] = self._task_cfg["env"]["learn"]["jointAccRewardScale"]
        self.rew_scales["action_rate"] = self._task_cfg["env"]["learn"]["actionRateRewardScale"]
        self.rew_scales["cosmetic"] = self._task_cfg["env"]["learn"]["cosmeticRewardScale"]
        self.rew_scales["energy_cost"] = self._task_cfg["env"]["learn"]["energyCostScale"]

        # command ranges
        self.command_x_range = self._task_cfg["env"]["randomCommandVelocityRanges"]["linear_x"]
        self.command_y_range = self._task_cfg["env"]["randomCommandVelocityRanges"]["linear_y"]
        self.command_yaw_range = self._task_cfg["env"]["randomCommandVelocityRanges"]["yaw"]

        # base init state
        pos = self._task_cfg["env"]["baseInitState"]["pos"]
        rot = self._task_cfg["env"]["baseInitState"]["rot"]
        v_lin = self._task_cfg["env"]["baseInitState"]["vLinear"]
        v_ang = self._task_cfg["env"]["baseInitState"]["vAngular"]
        state = pos + rot + v_lin + v_ang

        self.base_init_state = state

        # default joint positions
        self.named_default_joint_angles = self._task_cfg["env"]["defaultJointAngles"]

        # other
        # self.dt = 1 / 10
        self.dt = self._task_cfg["sim"]["dt"]
        self.max_episode_length_s = self._task_cfg["env"]["learn"]["episodeLength_s"]
        self.max_episode_length = int(self.max_episode_length_s / self.dt + 0.5)
        self.Kp = self._task_cfg["env"]["control"]["stiffness"]
        self.Kd = self._task_cfg["env"]["control"]["damping"]
        self.max_force = self._task_cfg["env"]["control"]["max_force"]

        for key in self.rew_scales.keys():
            self.rew_scales[key] *= self.dt

        self._num_envs = self._task_cfg["env"]["numEnvs"]
        self._bittle_translation = torch.tensor(self._task_cfg["env"]["baseInitState"]["pos"])
        print("self._bittle_translation:", self._bittle_translation)
        # torch.tensor([0.0, 0.0, 1.08])
        self._env_spacing = self._task_cfg["env"]["envSpacing"]
        # self._num_observations = 268 #FIX 观察指标： lin vel:3 + ang vel:3 + gravity:3 + commands:3  ++  dof_pos：8 + dof_vel:8  ++ actions:8 * 30 = 268
        self._num_observations = 25 + 8 * 8  # FIX 观察指标：rotations:3 +  commands:3  +  dof_pos：8  ++ actions:8 * 30 = 254 + actions:8
        self._num_actions = 8  # FIX - 和8个关节相关

        RLTask.__init__(self, name, env)
        print("RLTask init over")
        return

    def set_up_scene(self, scene) -> None:
        self.get_bittle()
        super().set_up_scene(scene)
        self._bittles = BittleView(prim_paths_expr="/World/envs/.*/bittle", name="bittleview")
        scene.add(self._bittles)
        scene.add(self._bittles._knees)
        scene.add(self._bittles._base)
        print("set_up_scene over")
        return

    def get_bittle(self):
        print("get bittle")
        bittle = Bittle(prim_path=self.default_zero_env_path + "/bittle", name="Bittle",
                        translation=self._bittle_translation)
        self._sim_config.apply_articulation_settings("Bittle", get_prim_at_path(bittle.prim_path),
                                                     self._sim_config.parse_actor_config("Bittle"))

        # Configure joint properties
        joint_paths = []
        for quadrant in ["left_front", "left_back", "right_front", "right_back"]:
            for component, sub in [("shoulder_link", "knee")]:
                joint_paths.append(f"{quadrant}_{component}/{quadrant}_{sub}_joint")
            joint_paths.append(f"base_frame_link/{quadrant}_shoulder_joint")
        for joint_path in joint_paths:
            set_drive(f"{bittle.prim_path}/{joint_path}", "angular", "position", 0, self.Kp, self.Kd,
                      self.max_force)  # FIX IT

        self.default_dof_pos = torch.zeros((self.num_envs, 8), dtype=torch.float, device=self.device,
                                           requires_grad=False)
        dof_names = bittle.dof_names
        for i in range(self.num_actions):
            name = dof_names[i]
            angle = self.named_default_joint_angles[name]
            self.default_dof_pos[:, i] = angle

    def get_observations(self) -> dict:
        # print("get obs:",time.time())
        torso_position, torso_rotation = self._bittles.get_world_poses(clone=False)
        ratation_angs = self._bittles.get_euler_positions()
        # root_velocities = self._bittles.get_velocities(clone=False)
        dof_pos = self._bittles.get_joint_positions(clone=False)
        dof_vel = self._bittles.get_joint_velocities(clone=False)
        # print("dof_vel:",dof_vel)
        # velocity = root_velocities[:, 0:3]
        # ang_velocity = root_velocities[:, 3:6]
        # base_lin_vel = quat_rotate_inverse(torso_rotation, velocity) * self.lin_vel_scale
        # base_ang_vel = quat_rotate_inverse(torso_rotation, ang_velocity) * self.ang_vel_scale
        projected_gravity = quat_rotate(torso_rotation, self.gravity_vec)
        # dof_pos_scaled = (dof_pos - self.default_dof_pos) * self.dof_pos_scale
        commands_scaled = self.commands * torch.tensor(
            [self.lin_vel_scale, self.lin_vel_scale, self.ang_vel_scale],
            requires_grad=False,
            device=self.commands.device,
        )
        # print("commands_scaled:",commands_scaled)
        # FIX IT
        flush_env_ids = (torch.remainder(self.progress_buf, 1) == 0).nonzero(as_tuple=False).squeeze(-1)
        # print("buf:",self.progress_buf)
        # print("remainder:",torch.remainder(self.progress_buf, 2))
        # print(flush_env_ids,torch.remainder(self.progress_buf, 1),self.progress_buf)
        # print("self.jointAngles_histories 0:",self.jointAngles_histories)
        if len(flush_env_ids) > 0:
            # print("---------flush_env_ids:",flush_env_ids)
            _histories = self.jointAngles_histories[flush_env_ids].cpu().detach().numpy()
            _targets = dof_pos[flush_env_ids].cpu().detach().numpy()
            # print(_histories.shape,_acts.shape)
            _histories_new = np.append(_histories, _targets, axis=1)
            self.jointAngles_histories[flush_env_ids] = torch.Tensor(
                np.delete(_histories_new, np.s_[0:8], axis=1)).cuda()
        # print("self.jointAngles_histories 1:",self.jointAngles_histories)
        # print(ratation_angs)
        obs = torch.cat(
            (
                # base_lin_vel,
                commands_scaled,
                ratation_angs,
                # base_ang_vel,
                projected_gravity,
                # ang_velocity * self.ang_vel_scale,
                (dof_pos - self.default_dof_pos) * self.dof_pos_scale,
                # dof_vel * self.dof_vel_scale,
                self.jointAngles_histories,  # FIX IT
                self.actions
            ),
            dim=-1,
        )
        self.obs_buf[:] = obs

        observations = {
            self._bittles.name: {
                "obs_buf": self.obs_buf
            }
        }
        return observations

    def pre_physics_step(self, actions) -> None:
        # print("pre_physics_step:",time.time())
        if not self._env._world.is_playing():
            return

        reset_env_ids = self.reset_buf.nonzero(as_tuple=False).squeeze(-1)
        if len(reset_env_ids) > 0:
            self.reset_idx(reset_env_ids)

        indices = torch.arange(self._bittles.count, dtype=torch.int32, device=self._device)
        self.actions[:] = actions.clone().to(self._device)
        current_targets = self.current_targets + self.action_scale * self.actions * self.dt

        #########################################################################################
        _current_targets = current_targets.cpu().detach().numpy()
        _current_targets = np.deg2rad(np.round(np.rad2deg(_current_targets)))  # Fix IT min 1 deg
        current_targets = torch.Tensor(_current_targets).to(self._device)
        ##########################################################################################
        self.current_targets[:] = tensor_clamp(current_targets, self.bittle_dof_lower_limits,
                                               self.bittle_dof_upper_limits)
        # print("self.current_targets2:",self.current_targets)
        self._bittles.set_joint_position_targets(self.current_targets, indices)
        self.torques = torch.clip(self.Kp * (self.current_targets - self.last_dof_pos) - self.Kd * self.last_dof_vel,
                                  -80., 80.)

    def reset_idx(self, env_ids):
        # print("reset_idx--------------------:",time.time())
        num_resets = len(env_ids)
        # randomize DOF velocities
        velocities = torch_rand_float(-0.1, 0.1, (num_resets, self._bittles.num_dof), device=self._device)  # FIX IT
        # print("vel:",velocities)
        dof_pos = self.default_dof_pos[env_ids]
        dof_vel = velocities

        self.current_targets[env_ids] = dof_pos[:]

        root_vel = torch.zeros((num_resets, 6), device=self._device)

        # apply resets
        indices = env_ids.to(dtype=torch.int32)
        self._bittles.set_joint_positions(dof_pos, indices)
        # FIX IT?
        self._bittles.set_joint_velocities(dof_vel, indices)
        self._bittles.set_world_poses(self.initial_root_pos[env_ids].clone(), self.initial_root_rot[env_ids].clone(),
                                      indices)
        self._bittles.set_velocities(root_vel, indices)

        self.commands_x[env_ids] = torch_rand_float(
            self.command_x_range[0], self.command_x_range[1], (num_resets, 1), device=self._device
        ).squeeze()
        self.commands_y[env_ids] = torch_rand_float(
            self.command_y_range[0], self.command_y_range[1], (num_resets, 1), device=self._device
        ).squeeze()
        self.commands_yaw[env_ids] = torch_rand_float(
            self.command_yaw_range[0], self.command_yaw_range[1], (num_resets, 1), device=self._device
        ).squeeze()

        # bookkeeping
        self.reset_buf[env_ids] = 0
        self.progress_buf[env_ids] = 0
        self.last_actions[env_ids] = 0.
        self.last_dof_vel[env_ids] = 0.
        self.last_dof_pos[env_ids] = 0.

        # FIX IT
        # print("?1:",self.jointAngles_histories[env_ids])
        self.jointAngles_histories[env_ids] = 0.
        self.last_positions[env_ids] = 0.

        # print("?2:",self.jointAngles_histories[env_ids])

    def post_reset(self):
        # print("post_reset")
        self.initial_root_pos, self.initial_root_rot = self._bittles.get_world_poses()
        self.current_targets = self.default_dof_pos.clone()

        dof_limits = self._bittles.get_dof_limits()
        self.bittle_dof_lower_limits = dof_limits[0, :, 0].to(device=self._device)
        self.bittle_dof_upper_limits = dof_limits[0, :, 1].to(device=self._device)

        self.commands = torch.zeros(self._num_envs, 3, dtype=torch.float, device=self._device, requires_grad=False)
        self.commands_y = self.commands.view(self._num_envs, 3)[..., 1]
        self.commands_x = self.commands.view(self._num_envs, 3)[..., 0]
        self.commands_yaw = self.commands.view(self._num_envs, 3)[..., 2]

        # initialize some data used later on
        self.extras = {}
        self.gravity_vec = torch.tensor([0.0, 0.0, -1], device=self._device).repeat(
            (self._num_envs, 1)
        )
        self.actions = torch.zeros(
            self._num_envs, self.num_actions, dtype=torch.float, device=self._device, requires_grad=False
        )
        self.last_dof_vel = torch.zeros((self._num_envs, 8), dtype=torch.float, device=self._device,
                                        requires_grad=False)
        self.last_dof_pos = torch.zeros((self._num_envs, 8), dtype=torch.float, device=self._device,
                                        requires_grad=False)
        self.last_actions = torch.zeros(self._num_envs, self.num_actions, dtype=torch.float, device=self._device,
                                        requires_grad=False)
        self.last_positions = torch.zeros(self._num_envs, 3, dtype=torch.float, device=self._device,
                                          requires_grad=False)
        self.torques = torch.zeros(self._num_envs, 1, dtype=torch.float, device=self._device, requires_grad=False)
        self.prev_torques = self.torques.clone()

        self.time_out_buf = torch.zeros_like(self.reset_buf)

        # FIX IT
        self.jointAngles_histories = torch.tensor([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], device=self._device).repeat(
            (self._num_envs, 8)
        )

        # randomize all envs
        indices = torch.arange(self._bittles.count, dtype=torch.int64, device=self._device)
        self.reset_idx(indices)

    def calculate_metrics(self) -> None:
        # print("calculate_metrics:",time.time())
        torso_position, torso_rotation = self._bittles.get_world_poses(clone=False)
        root_velocities = self._bittles.get_velocities(clone=False)
        dof_pos = self._bittles.get_joint_positions(clone=False)
        dof_vel = self._bittles.get_joint_velocities(clone=False)
        #
        velocity = root_velocities[:, 0:3]
        ang_velocity = root_velocities[:, 3:6]
        #
        base_lin_vel = quat_rotate_inverse(torso_rotation, velocity)
        base_ang_vel = quat_rotate_inverse(torso_rotation, ang_velocity)
        #
        # # velocity tracking reward
        lin_vel_error = torch.sum(torch.square(self.commands[:, :2] - base_lin_vel[:, :2]), dim=1)
        ang_vel_error = torch.square(self.commands[:, 2] - base_ang_vel[:, 2])
        rew_lin_vel_xy = torch.exp(-lin_vel_error / 0.25) * self.rew_scales["lin_vel_xy"]
        rew_ang_vel_z = torch.exp(-ang_vel_error / 0.25) * self.rew_scales["ang_vel_z"]  # FIX IT
        rew_lin_vel_z = torch.square(base_lin_vel[:, 2]) * self.rew_scales["lin_vel_z"]

        #
        # #print("-:", base_lin_vel[:, :2],lin_vel_error, rew_lin_vel_xy, base_lin_vel[:, 2], rew_lin_vel_z)
        #
        rew_joint_acc = torch.sum(torch.square(self.last_dof_vel - dof_vel), dim=1) * self.rew_scales["joint_acc"]
        rew_action_rate = torch.sum(torch.square(self.last_actions - self.actions), dim=1) * self.rew_scales[
            "action_rate"]
        # rew_cosmetic = torch.sum(torch.abs(dof_pos[:, 0:4] - self.default_dof_pos[:, 0:4]), dim=1) * self.rew_scales["cosmetic"] #FIX IT


        ###############################################################################################
        # torch.diag(torch.mm(x,y.T))
        # torques = torch.clip(self.Kp*(self.current_targets - self.last_dof_pos) - self.Kd*self.last_dof_vel, -80., 80.)
        # torques = torch.clip(self.Kp*(self.current_targets - self.last_dof_pos) - self.Kd*self.last_dof_vel, -80., 80.)
        # torques = 1 * (self.default_dof_pos - dof_pos) + 0.0 * (dof_vel)
        # print("torques0:",torques,self.current_targets,dof_pos)
        # torques = torch.sum(torch.abs(_torques))
        # print("torques1:",torch.diag(torch.abs(torch.mm(torques,dof_vel.T))))
        # print("torques3:",torch.diag(torch.abs(torch.mm(torques,dof_vel.T))))
        # energy_cost = torch.diag(torch.mm(torques,dof_vel.T))
        # rew_energy_cost = torch.exp(-energy_cost / 0.25) * self.rew_scales["energy_cost"]
        rew_energy_cost = torch.sum(torch.square(self.prev_torques - self.torques), dim=1) * self.rew_scales[
            "energy_cost"]
        # rew_torque = torch.sum(torch.square(torques), dim=1)
        # print(":",rew_torque)
        # rew_energy_cost = rew_torque * self.rew_scales["energy_cost"]
        # print(":",energy_cost,rew_energy_cost)
        ###############################################################################################
        total_reward = rew_lin_vel_xy + rew_action_rate + rew_lin_vel_z + rew_ang_vel_z + rew_joint_acc + rew_energy_cost
        total_reward = torch.clip(total_reward, 0.0, None)

        self.last_actions[:] = self.actions[:]
        self.last_dof_vel[:] = dof_vel[:]
        self.last_dof_pos[:] = dof_pos[:]
        self.last_positions[:] = torso_position[:].clone()

        # self.fallen_over = self._bittles.is_base_below_threshold(threshold=0.51, ground_heights=0.0)
        # self.fallen_over = self._bittles.is_base_below_threshold(threshold=0.51, ground_heights=0.0) | self._bittles.is_knee_below_threshold(threshold=0.21, #ground_heights=0.0)

        self.fallen_over = self._bittles.is_orientation_below_threshold(threshold=0.5) | \
                           self._bittles.is_knee_below_threshold(threshold=0.2,
                                                                 ground_heights=0.0)  # FIX 0.2 is lowest threshold

        # self.fallen_over = self._bittles.is_orientation_below_threshold(threshold=0.90)
        # print("fallen:",self.fallen_over)
        total_reward[torch.nonzero(self.fallen_over)] = -1
        self.rew_buf[:] = total_reward.detach()

    def is_done(self) -> None:
        # reset agents
        time_out = self.progress_buf >= self.max_episode_length - 1
        self.reset_buf[:] = time_out | self.fallen_over
        # print("self.progress_buf:",self.progress_buf)
        # print("timeout:",time_out)


