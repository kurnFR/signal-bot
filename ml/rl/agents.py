"""
Reinforcement Learning Agents for Financial Trading.
Implements fast, CPU-optimized agents:
1. DQNAgent: Deep Q-Network with experience replay and target network.
2. PolicyGradientAgent: Direct policy gradient optimization for optimal position sizing.
"""
from __future__ import annotations

import json
from pathlib import Path
import random
from typing import Any, Tuple

import numpy as np


class ReplayBuffer:
    def __init__(self, capacity: int = 10000):
        self.capacity = capacity
        self.buffer = []
        self.position = 0

    def push(self, state: np.ndarray, action: int, reward: float, next_state: np.ndarray, done: bool):
        if len(self.buffer) < self.capacity:
            self.buffer.append(None)
        self.buffer[self.position] = (state, action, reward, next_state, done)
        self.position = (self.position + 1) % self.capacity

    def sample(self, batch_size: int) -> list:
        return random.sample(self.buffer, min(len(self.buffer), batch_size))

    def __len__(self) -> int:
        return len(self.buffer)


class NeuralNetwork:
    """Lightweight 2-layer MLP implemented in pure NumPy for CPU efficiency."""

    def __init__(self, input_dim: int, hidden_dim: int, output_dim: int, lr: float = 0.001):
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.output_dim = output_dim
        self.lr = lr

        # He initialization
        self.w1 = np.random.randn(input_dim, hidden_dim).astype(np.float32) * np.sqrt(2.0 / input_dim)
        self.b1 = np.zeros((1, hidden_dim), dtype=np.float32)
        self.w2 = np.random.randn(hidden_dim, output_dim).astype(np.float32) * np.sqrt(2.0 / hidden_dim)
        self.b2 = np.zeros((1, output_dim), dtype=np.float32)

    def forward(self, x: np.ndarray) -> np.ndarray:
        if x.ndim == 1:
            x = x.reshape(1, -1)
        self.x = x
        self.z1 = np.dot(x, self.w1) + self.b1
        self.a1 = np.maximum(0.0, self.z1)  # ReLU
        self.z2 = np.dot(self.a1, self.w2) + self.b2
        return self.z2

    def backward(self, grad_output: np.ndarray) -> None:
        grad_w2 = np.dot(self.a1.T, grad_output)
        grad_b2 = np.sum(grad_output, axis=0, keepdims=True)

        grad_a1 = np.dot(grad_output, self.w2.T)
        grad_z1 = grad_a1 * (self.z1 > 0)  # ReLU grad

        grad_w1 = np.dot(self.x.T, grad_z1)
        grad_b1 = np.sum(grad_z1, axis=0, keepdims=True)

        # Gradient clipping for numerical stability
        np.clip(grad_w1, -1.0, 1.0, out=grad_w1)
        np.clip(grad_w2, -1.0, 1.0, out=grad_w2)

        # SGD step
        self.w1 -= self.lr * grad_w1
        self.b1 -= self.lr * grad_b1
        self.w2 -= self.lr * grad_w2
        self.b2 -= self.lr * grad_b2

    def copy_weights_from(self, source: NeuralNetwork) -> None:
        self.w1 = np.copy(source.w1)
        self.b1 = np.copy(source.b1)
        self.w2 = np.copy(source.w2)
        self.b2 = np.copy(source.b2)


class DQNAgent:
    """Deep Q-Network Agent for FinRL-style trading."""

    def __init__(
        self,
        state_dim: int,
        action_dim: int = 3,
        hidden_dim: int = 64,
        lr: float = 0.001,
        gamma: float = 0.95,
        epsilon: float = 1.0,
        epsilon_min: float = 0.05,
        epsilon_decay: float = 0.995,
        buffer_capacity: int = 10000,
        batch_size: int = 64,
        target_update_freq: int = 100,
    ):
        self.state_dim = state_dim
        self.action_dim = action_dim
        self.gamma = gamma
        self.epsilon = epsilon
        self.epsilon_min = epsilon_min
        self.epsilon_decay = epsilon_decay
        self.batch_size = batch_size
        self.target_update_freq = target_update_freq
        self.total_steps = 0

        self.policy_net = NeuralNetwork(state_dim, hidden_dim, action_dim, lr=lr)
        self.target_net = NeuralNetwork(state_dim, hidden_dim, action_dim, lr=lr)
        self.target_net.copy_weights_from(self.policy_net)

        self.buffer = ReplayBuffer(capacity=buffer_capacity)

    def select_action(self, state: np.ndarray, evaluate: bool = False) -> int:
        if not evaluate and random.random() < self.epsilon:
            return random.randint(0, self.action_dim - 1)

        q_values = self.policy_net.forward(state)
        return int(np.argmax(q_values[0]))

    def train_step(self) -> float | None:
        if len(self.buffer) < self.batch_size:
            return None

        batch = self.buffer.sample(self.batch_size)
        states = np.array([item[0] for item in batch], dtype=np.float32)
        actions = np.array([item[1] for item in batch], dtype=int)
        rewards = np.array([item[2] for item in batch], dtype=np.float32)
        next_states = np.array([item[3] for item in batch], dtype=np.float32)
        dones = np.array([item[4] for item in batch], dtype=bool)

        current_q = self.policy_net.forward(states)
        next_q = self.target_net.forward(next_states)
        max_next_q = np.max(next_q, axis=1)

        targets = current_q.copy()
        for i in range(len(batch)):
            if dones[i]:
                targets[i, actions[i]] = rewards[i]
            else:
                targets[i, actions[i]] = rewards[i] + self.gamma * max_next_q[i]

        loss_grad = (current_q - targets) / len(batch)
        self.policy_net.backward(loss_grad)

        self.total_steps += 1
        if self.total_steps % self.target_update_freq == 0:
            self.target_net.copy_weights_from(self.policy_net)

        if self.epsilon > self.epsilon_min:
            self.epsilon *= self.epsilon_decay

        return float(np.mean((current_q - targets) ** 2))

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            w1=self.policy_net.w1,
            b1=self.policy_net.b1,
            w2=self.policy_net.w2,
            b2=self.policy_net.b2,
            meta=json.dumps({
                "state_dim": self.state_dim,
                "action_dim": self.action_dim,
                "hidden_dim": self.policy_net.hidden_dim,
                "total_steps": self.total_steps,
            }),
        )

    def load(self, path: str | Path) -> None:
        data = np.load(path, allow_pickle=True)
        self.policy_net.w1 = data["w1"]
        self.policy_net.b1 = data["b1"]
        self.policy_net.w2 = data["w2"]
        self.policy_net.b2 = data["b2"]
        self.target_net.copy_weights_from(self.policy_net)


class PolicyGradientAgent:
    """Direct Policy Gradient (REINFORCE) Agent for position assignment."""

    def __init__(self, state_dim: int, action_dim: int = 3, lr: float = 0.005, gamma: float = 0.98):
        self.state_dim = state_dim
        self.action_dim = action_dim
        self.lr = lr
        self.gamma = gamma

        # Linear-Softmax policy weights: shape (state_dim, action_dim)
        self.weights = np.random.randn(state_dim, action_dim).astype(np.float32) * 0.01
        self.bias = np.zeros((1, action_dim), dtype=np.float32)

        # Episode memory
        self.episode_states = []
        self.episode_actions = []
        self.episode_rewards = []

    def get_action_probabilities(self, state: np.ndarray) -> np.ndarray:
        if state.ndim == 1:
            state = state.reshape(1, -1)
        logits = np.dot(state, self.weights) + self.bias
        exp_logits = np.exp(logits - np.max(logits, axis=1, keepdims=True))
        return exp_logits / np.sum(exp_logits, axis=1, keepdims=True)

    def select_action(self, state: np.ndarray, evaluate: bool = False) -> int:
        probs = self.get_action_probabilities(state)[0]
        if evaluate:
            return int(np.argmax(probs))
        return int(np.random.choice(self.action_dim, p=probs))

    def record_step(self, state: np.ndarray, action: int, reward: float) -> None:
        self.episode_states.append(state)
        self.episode_actions.append(action)
        self.episode_rewards.append(reward)

    def train_episode(self) -> float:
        if not self.episode_rewards:
            return 0.0

        n = len(self.episode_rewards)
        discounted_rewards = np.zeros(n, dtype=np.float32)
        running_sum = 0.0
        for t in reversed(range(n)):
            running_sum = self.episode_rewards[t] + self.gamma * running_sum
            discounted_rewards[t] = running_sum

        # Baseline normalization
        mean = np.mean(discounted_rewards)
        std = np.std(discounted_rewards) + 1e-8
        advantages = (discounted_rewards - mean) / std

        states = np.array(self.episode_states, dtype=np.float32)
        probs = self.get_action_probabilities(states)

        grad_w = np.zeros_like(self.weights)
        grad_b = np.zeros_like(self.bias)

        for t in range(n):
            s = states[t:t+1]
            a = self.episode_actions[t]
            adv = advantages[t]

            # Softmax derivative: p_i * adv for all, with (1 - p_a) for chosen action
            d_logits = probs[t:t+1].copy()
            d_logits[0, a] -= 1.0
            d_logits *= adv

            grad_w += np.dot(s.T, d_logits)
            grad_b += d_logits

        np.clip(grad_w, -1.0, 1.0, out=grad_w)
        self.weights -= self.lr * grad_w / n
        self.bias -= self.lr * grad_b / n

        self.episode_states.clear()
        self.episode_actions.clear()
        self.episode_rewards.clear()
        return float(np.mean(discounted_rewards))

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, weights=self.weights, bias=self.bias)

    def load(self, path: str | Path) -> None:
        data = np.load(path, allow_pickle=True)
        self.weights = data["weights"]
        self.bias = data["bias"]

