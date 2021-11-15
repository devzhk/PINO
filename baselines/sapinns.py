import csv
import random
from timeit import default_timer
import deepxde as dde
import numpy as np
from baselines.data import NSdata
import torch

from tensordiffeq.boundaries import DomainND, periodicBC
from .tqd_utils import PointsIC

Re = 500


def forcing(x):
    return - 4 * torch.cos(4 * x[:, 1:2])


def pde(x, u):
    '''
    Args:
        x: (x, y, t)
        u: (u, v, w), where (u,v) is the velocity, w is the vorticity
    Returns: list of pde loss

    '''
    u_vel, v_vel, w = u[:, 0:1], u[:, 1:2], u[:, 2:3]

    u_vel_x = dde.grad.jacobian(u, x, i=0, j=0)
    u_vel_xx = dde.grad.hessian(u, x, component=0, i=0, j=0)
    u_vel_yy = dde.grad.hessian(u, x, component=0, i=1, j=1)

    v_vel_y = dde.grad.jacobian(u, x, i=1, j=1)
    v_vel_xx = dde.grad.hessian(u, x, component=1, i=0, j=0)
    v_vel_yy = dde.grad.hessian(u, x, component=1, i=1, j=1)

    w_vor_x = dde.grad.jacobian(u, x, i=2, j=0)
    w_vor_y = dde.grad.jacobian(u, x, i=2, j=1)
    w_vor_t = dde.grad.jacobian(u, x, i=2, j=2)

    w_vor_xx = dde.grad.hessian(u, x, component=2, i=0, j=0)
    w_vor_yy = dde.grad.hessian(u, x, component=2, i=1, j=1)

    eqn1 = w_vor_t + u_vel * w_vor_x + v_vel * w_vor_y - \
           1 / Re * (w_vor_xx + w_vor_yy) - forcing(x)
    eqn2 = u_vel_x + v_vel_y
    eqn3 = u_vel_xx + u_vel_yy + w_vor_y
    eqn4 = v_vel_xx + v_vel_yy - w_vor_x
    return [eqn1, eqn2, eqn3, eqn4]


def eval(model, dataset,
         step, time_cost,
         offset, config):
    '''
    evaluate test error for the model over dataset
    '''
    test_points, test_vals = dataset.get_test_xyt()

    pred = model.predict(test_points)
    vel_u_truth = test_vals[:, 0]
    vel_v_truth = test_vals[:, 1]
    vor_truth = test_vals[:, 2]

    vel_u_pred = pred[:, 0]
    vel_v_pred = pred[:, 1]
    vor_pred = pred[:, 2]

    u_err = dde.metrics.l2_relative_error(vel_u_truth, vel_u_pred)
    v_err = dde.metrics.l2_relative_error(vel_v_truth, vel_v_pred)
    vor_err = dde.metrics.l2_relative_error(vor_truth, vor_pred)
    print(f'Instance index : {offset}')
    print(f'L2 relative error in u: {u_err}')
    print(f'L2 relative error in v: {v_err}')
    print(f'L2 relative error in vorticity: {vor_err}')
    with open(config['log']['logfile'], 'a') as f:
        writer = csv.writer(f)
        writer.writerow([offset, u_err, v_err, vor_err, step, time_cost])


def train_sapinn(offset, config, args):
    seed = random.randint(1, 10000)
    print(f'Random seed :{seed}')
    np.random.seed(seed)
    # construct dataloader
    data_config = config['data']
    if 'datapath2' in data_config:
        dataset = NSdata(datapath1=data_config['datapath'],
                         datapath2=data_config['datapath2'],
                         offset=offset, num=1,
                         nx=data_config['nx'], nt=data_config['nt'],
                         sub=data_config['sub'], sub_t=data_config['sub_t'],
                         vel=True,
                         t_interval=data_config['time_interval'])
    else:
        dataset = NSdata(datapath1=data_config['datapath'],
                         offset=offset, num=1,
                         nx=data_config['nx'], nt=data_config['nt'],
                         sub=data_config['sub'], sub_t=data_config['sub_t'],
                         vel=True,
                         t_interval=data_config['time_interval'])
    domain = DomainND(['x', 'y', 't'], time_var='t')
    domain.add('x', [0.0, 2 * np.pi], dataset.S)
    domain.add('y', [0.0, 2 * np.pi], dataset.S)
    domain.add('t', [0.0, data_config['time_interval']], dataset.T)
    domain.generate_collocation_points(config['train']['num_domain'])
    init_vals = dataset.get_init_cond()
    num_inits = config['train']['num_init']
    if num_inits > dataset.S ** 2:
        num_inits = dataset.S ** 2
    init_cond = PointsIC(domain, init_vals, var=['x', 'y'], n_values=num_inits)
    bd_cond = periodicBC(domain, ['x', 'y'], n_values=config['train']['num_boundary'])
    