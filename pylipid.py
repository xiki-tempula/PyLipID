#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug 28 19:28:17 2019

@author: Wanling Song

"""
import mdtraj as md
import numpy as np
import pandas as pd
import argparse
import sys
from collections import defaultdict
import pickle
import os
from scipy import stats
import matplotlib.pyplot as plt
import matplotlib 
matplotlib.use('Agg')
import networkx as nx
import seaborn as sns
import matplotlib.patches as patches
from scipy.optimize import curve_fit
from scipy.sparse import coo_matrix
from scipy import sparse
import community


########################################
### Loading calculation parameters  ####
########################################

parser = argparse.ArgumentParser()
parser.add_argument("-f", nargs="+", metavar="./run/md.xtc", help="List of trajectories, seperated by space, \
                     Supports xtc, gro format. Used by mdtraj.load()")
parser.add_argument("-c", nargs="+", metavar="./run/system.gro", \
                    help="List of coordinates of trajectory, in the same order as -f, required when inputs of -f are xtc trajectories, \
                    Supported format: gro, pdb, etc., Used by mdtraj.load()")
parser.add_argument("-tu", default="us", choices=["ns", "us"], metavar="us", \
                    help="Time unit for interaction duration calculation. Available options: ns, us. This will affect the unit of koff as well.")
parser.add_argument("-save_dir", default=None, metavar="None", help="The directory where all the generated results will be put in. \
                    The directory will be created if not existing. Using the current working directory if not specified.")
parser.add_argument("-cutoffs", nargs=2, default=(0.55, 1.4), metavar=(0.55, 1.4), \
                    help="Double cutoff seperated by space. In unit of nm. Default: 0.55 1.4")
parser.add_argument("-lipids", nargs="+", metavar="POPC", default="POPC CHOL POP2", \
                    help="Lipid species to check, seperated by space. Using the Martini force field nonmenclature")
parser.add_argument("-lipid_atoms", nargs="+", metavar="PO4", default=None, \
                    help="Lipid atoms to check, seperated by space. Using the Martini force field nonmenclature")
parser.add_argument("-nprot", default=1, metavar="1", \
                    help="num. of protein in the simulation system, compatible with systems containing multiple copies of the same protein")
parser.add_argument("-resi_offset", default=0, metavar="0", \
                    help="Shifting the residue index. Usful when using martinize.py for proteins with missing residues at N-terminus.")
parser.add_argument("-natoms_per_protein", default=None,  metavar="None", \
                    help="Number of atoms/beads the protein contains, esp useful when the system has multiple copies \
                    of the protein. If not specificied, the algorithm will deduce it by dividing the num. of atoms in the selection of 'protein' by num. of proteins that \
                    is defined by -nprot.")
parser.add_argument("-plot_koff", nargs="?", default=True, const=True, metavar="True", help="Plot koff values for each reasidue based on the conglomerate interaction durations from all trajectories. \
                    This means the koff is an average over all trajectories. A directory koff_{lipid} will be genereated for each lipid species. ")
parser.add_argument("-save_dataset", nargs="?", default=True, const=True, metavar="True", help="Save dataset in Pickle")
parser.add_argument("-helix_regions", nargs="*", metavar="8,36", default="",
                    help="Label the helix locations by blue bars in lipid interaction plots.")


args = parser.parse_args(sys.argv[1:])

##########################################
########## assisting functions ###########
##########################################

def get_protein_idx_per_residue(traj, resi_offset, residue_set, nresi_per_protein, atom_idx_start, atom_idx_end):
    atom_list = [(atom_idx, "{}{}".format(traj.topology.atom(atom_idx).residue.index%nresi_per_protein+resi_offset+1, \
                  traj.topology.atom(atom_idx).residue.name)) for atom_idx in np.arange(atom_idx_start, atom_idx_end)]
    selected_idx = [np.where(np.array(atom_list)[:,1]==residue)[0] for residue in residue_set]
    return [np.array(np.array(atom_list)[:, 0], dtype=int)[idx_set] for idx_set in selected_idx]

def get_atom_index_for_lipid(lipid, traj, part=None):
    whole_atom_index = [atom.index for atom in traj.topology.atoms if atom.residue.name == lipid]
    if part != None:
        parts_atom_index = [traj.topology.atom(idx).index for idx in whole_atom_index if traj.topology.atom(idx).name in part]
        return parts_atom_index
    else:
        return whole_atom_index

def find_contact(traj, query_atoms, haystack_atoms, cutoff_low=0.60, cutoff_high=1.4):
    """
    compute the contact of query_atoms with haystack_atoms, and return for each frame a list of
    residues which are in contact with the query in that frame. The output is in format of
    a list of numpy array
    """
    contact_atoms_low = md.compute_neighbors(traj, cutoff_low, query_atoms, haystack_atoms)
    contact_atoms_high = md.compute_neighbors(traj, cutoff_high, query_atoms, haystack_atoms)
    contact_residues_low = []
    for contact in contact_atoms_low:
        contact_residues_low.append(atom2residue(contact, traj))
    contact_residues_high = []
    for contact in contact_atoms_high:
        contact_residues_high.append(atom2residue(contact, traj))
    return contact_residues_low, contact_residues_high

def atom2residue(atom_list, traj):
    """
    Take the atom list and return the residue list, get rid of the deplicates
    """
    ## switch to residues ###
    residues_raw = ["{}{}".format(traj.topology.atom(atom_index).residue.index, \
                    traj.topology.atom(atom_index).residue.name) for atom_index in atom_list]
    ### remove duplicates ###
    residue_list = np.unique(residues_raw)
    return residue_list


class Durations():
    def __init__(self, contact_residues_low, contact_residue_high, dt):
        self.contact_low = contact_residues_low
        self.contact_high = contact_residue_high
        self.dt = dt

    def cal_duration(self):
        self.pointer = [np.zeros_like(self.contact_high[idx], dtype=np.int) for idx in range(len(self.contact_high))]
        durations = []
        for i in range(len(self.contact_low)):
            for j in range(len(self.contact_low[i])):
                pos = np.where(self.contact_high[i] == self.contact_low[i][j])[0][0]
                if self.pointer[i][pos] == 0:
                    durations.append(self.get_duration(i, pos))
        if len(durations) == 0:
            return [0]
        else:
            return durations

    def get_duration(self, i, j):
        count = 1
        self.pointer[i][j] = 1
        lipid_to_search = self.contact_high[i][j]
        for k in range(i+1, len(self.contact_high)):
            locations = np.where(self.contact_high[k] == lipid_to_search)[0]
            if len(locations) == 0:
                return count * self.dt
            else:
                pos = locations[0]
                self.pointer[k][pos] = 1
                count +=1
        return count * self.dt


def cal_interaction_intensity(contact_residues_low):
    """
    The probablily of finding the lipids around the selected residue plus the number of
    lipids found around the selected residue, the average number of lipid per contact
    """
    contact_counts = [len(item) for item in contact_residues_low]
    mask = np.array(contact_counts) > 0
    contact_counts_nonzero = np.array(contact_counts)[mask]
    return 100 * len(contact_counts_nonzero)/len(contact_residues_low), np.nan_to_num(contact_counts_nonzero.mean())


def cal_sigma(durations, num_of_lipids, T_total, delta_t_range):
    sigma = {}
    for delta_t in delta_t_range:
        if delta_t == 0:
            sigma[delta_t] = 1
            sigma0 = float(sum([restime - delta_t for restime in durations if restime >= delta_t])) / ((T_total - delta_t) * num_of_lipids)
        else:
            try:
                sigma[delta_t] = float(sum([restime - delta_t for restime in durations if restime >= delta_t])) / ((T_total - delta_t) * num_of_lipids * sigma0)
            except ZeroDivisionError:
                sigma[delta_t] = 0    
    return sigma


def cal_restime_koff(sigma, initial_guess):
    """
    fit the exponential curve y=Ae^(-kx)
    """
    delta_t_range = list(sigma.keys())
    delta_t_range.sort() # x
    hist_values = [sigma[delta_t] for delta_t in delta_t_range] # y
    popt, pcov = curve_fit(mono_expo, delta_t_range, hist_values, p0=initial_guess, maxfev=6000)
    koff = abs(popt[1])
    A = popt[0]
    restime = 1/koff
    return restime, koff, A

def mono_expo(x, A, k):
    return A*np.exp(-k*x)

def graph_network(graph_object,outputfilename, interaction_strength=np.array([0]), \
                layout='spring', node_labels=False, node_colour='r'):
    """
    Plot interaction network 
    """
    plt.rcParams["font.size"] = 8
    plt.rcParams["font.weight"] = "bold"
    plt.cla()
    fig = plt.figure(1, figsize=(6,6))
    if layout == 'spring':
        pos=nx.spring_layout(graph_object)
    elif layout == 'circular':
        pos=nx.circular_layout(graph_object)
    if interaction_strength.shape == (1,):
        nx.draw_networkx_nodes(graph_object, pos, node_color="Red", edgecolor="gray" )
    else:
        nx.draw_networkx_nodes(graph_object, pos, node_size = interaction_strength, \
                               node_color="Red", edgecolor="gray")
    if node_labels == False:
        label_dict = dict(zip(graph_object.nodes(), np.array(graph_object.nodes())+1))
    else:
        label_dict = dict(zip(graph_object.nodes(), node_labels))
    nx.draw_networkx_labels(graph_object,pos,font_size=8, labels = label_dict, font_weight="bold")
    weights = np.array([data[2]['weight'] for data in graph_object.edges(data=True)])
    nx.draw_networkx_edges(graph_object,pos, width=weights, edge_color="black", alpha=0.6)
    plt.axis('off')
    plt.savefig("{}.tiff".format(outputfilename), dpi=200)
    plt.close()
    return


def graph_koff(duration_raw, sigma, koff, A, timeunit, residue, outputfilename):
    if timeunit == "ns":
        xlabel = "Duration (ns)"
    elif timeunit == "us":
        xlabel = r"Duration ($\mu s$)"
    fig = plt.figure(1, figsize=(6.0, 3.5))
    left, width = 0.12, 0.35
    bottom, height = 0.17, 0.75
    left_h = left + width + 0.05
    rect_scatter = [left, bottom, width, height]
    rect_histy = [left_h, bottom, 0.35, height]
    axScatter = plt.axes(rect_scatter)
    axHisty = plt.axes(rect_histy)
    x = np.sort(duration_raw)
    y = np.arange(len(x)) + 1
    axScatter.scatter(x[::-1], y, label=residue)
    axScatter.set_xlim(0, x[-1] * 1.1)
    axScatter.legend(loc="upper right", prop={"size": 10, "weight": "bold"}, frameon=False)
    axScatter.set_ylabel("Sorted Index", fontsize=10, weight="bold")
    axScatter.set_xlabel(xlabel, fontsize=10, weight="bold")
    delta_t_range = list(sigma.keys())
    delta_t_range.sort()
    hist_values = [sigma[delta_t] for delta_t in delta_t_range]
    axHisty.scatter(delta_t_range, hist_values)
    axHisty.yaxis.set_label_position("right")
    axHisty.yaxis.tick_right()
    axHisty.set_xlabel(r"$\Delta t$", fontsize=10, weight="bold")
    axHisty.set_ylabel("Probability", fontsize=10, weight="bold")
    axHisty.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    axHisty.set_ylim(-0.1, 1.1)
    n_fitted = mono_expo(np.array(delta_t_range), A, koff)
    r_squared = (stats.linregress(np.nan_to_num(n_fitted), np.nan_to_num(hist_values))[2])**2
    if timeunit == "ns":
        axHisty.plot(delta_t_range, n_fitted, 'r--', linewidth=3, \
                     label="$k_{{off}}$ = {:.3f} ns$^{{-1}} $\n$R^2$ = {:.4f}".format(float(koff), r_squared))
    elif timeunit == "us":
        axHisty.plot(delta_t_range, n_fitted, 'r--', linewidth=3, \
                     label="$k_{{off}}$ = {:.3f} $\mu s^{{-1}} $\n$R^2$ = {:.4f}".format(float(koff), r_squared))
    axHisty.legend(loc='upper right', prop={"size": 10, "weight": "bold"}, frameon=False)
    plt.savefig(outputfilename, dpi=200)
    plt.close()
    return


def identify_helix_region(ax, ylim, helix_regions):
    for (x1, x2) in helix_regions:
        p = patches.Rectangle((x1, ylim*0.9), (x2-x1), ylim*0.07, fill=True, edgecolor=None, linewidth=0, facecolor=sns.xkcd_rgb["azure"], alpha=0.5)
        ax.add_patch(p)
    return

def check_dir(save_dir, suffix=None):

    if save_dir == None and suffix==None:
        return save_dir
    elif save_dir == None:
        save_dir = os.getcwd()
    else:
        save_dir = save_dir + "/{}".format(suffix)
    if not os.path.isdir(save_dir):
        print("Creating new director: {}".format(save_dir))
        os.mkdir(save_dir)
    else:
        print("{} already exists!\nCaution: files may be overwritten!".format(save_dir))
    return save_dir

def sparse_corrcoef(A, B=None):

    if B is not None:
        A = sparse.vstack((A, B), format='csr')

    A = A.astype(np.float64)
    n = A.shape[1]

    # Compute the covariance matrix
    rowsum = A.sum(1)
    centering = rowsum.dot(rowsum.T.conjugate()) / n
    C = (A.dot(A.T.conjugate()) - centering) / (n - 1)

    # The correlation coefficients are given by
    # C_{i,j} / sqrt(C_{i} * C_{j})
    d = np.diag(C)
    coeffs = C / np.sqrt(np.outer(d, d))

    return coeffs


#####################################
####### Main Class object ###########
#####################################


class LipidInteraction():

    def __init__(self, trajfile_list, grofile_list=None, cutoff=[0.55, 1.4], \
                 lipid="POPC", lipid_atoms=None, nprot=1, natoms_per_protein=None, resi_offset=0, save_dir=None, timeunit="us"):
        if grofile_list != None:
            assert len(trajfile_list) == len(grofile_list), \
            "List of coordinates should be in the same order and length of list of trajectories!"

        self.save_dir = check_dir(save_dir, "")
        self.trajfile_list = trajfile_list
        self.grofile_list = grofile_list
        self.nrepeats = len(self.trajfile_list)
        self.cutoff = np.sort(np.array(cutoff, dtype=float))
        self.lipid = lipid
        self.lipid_atoms = lipid_atoms
        self.nprot = int(nprot)
        self.resi_offset = int(resi_offset)
        self.koff = {}
        self.sigmas = {}
        self.A = {}
        self.timeunit = timeunit
        self.interaction_duration_raw = defaultdict(list)
        self.interaction_duration = defaultdict(list)
        self.interaction_occupancy = defaultdict(list)
        self.lipid_count = defaultdict(list)

        ############ do some checking and load params ##########
        if natoms_per_protein == None:
            self.natoms_per_protein = []
            for trajfile, grofile in zip(self.trajfile_list, self.grofile_list):
                traj = md.load(trajfile, top=grofile)
                self.natoms_per_protein.append(int(len(traj.top.select("protein"))/self.nprot))

            assert all(elem == self.natoms_per_protein[0] for elem in self.natoms_per_protein), \
            "The list of trajectories contains different ssytem setup (\
            different n. of proteins or different proteins)"
            self.natoms_per_protein = int(self.natoms_per_protein[0])

        else:
            self.natoms_per_protein = natoms_per_protein
        ####### after this check, it's assumed that all trajs have the same setup #########
        _traj = md.load(trajfile_list[0], top=grofile_list[0])
        self.nresi_per_protein = _traj.topology.atom(self.natoms_per_protein).residue.index
        ###################################################
        residue_set = []
        for atom_idx in np.arange(self.natoms_per_protein):
            resi = "{}{}".format(_traj.topology.atom(atom_idx).residue.index + self.resi_offset + 1, \
                    _traj.topology.atom(atom_idx).residue.name)
            if not resi in residue_set:
                residue_set.append(resi)
        self.residue_set = np.array(residue_set)
        ####################################################
        self.protein_residue_indices_set = [get_protein_idx_per_residue(_traj, self.resi_offset, self.residue_set, self.nresi_per_protein, \
                                                                        protein_idx*self.natoms_per_protein, (protein_idx+1)*self.natoms_per_protein) \
                                            for protein_idx in range(self.nprot)]
        return


    def cal_interactions(self, save_dir=None, plot_koff=True, save_dataset=True):

        if save_dir == None:
            self.save_dir = check_dir(self.save_dir, "Interaction_{}".format(self.lipid))
        else:
            self.save_dir = check_dir(save_dir, "Interaction_{}".format(self.lipid))

        initial_guess = (1, 1) if self.timeunit == "us" else (500, 0.001)
        converter = 1/10000000.0 if self.timeunit == "us" else 1/1000.0

        with open("{}/calculation_log_{}.txt".format(self.save_dir, self.lipid), "w") as f:
            f.write("Lipid to check: {}\n".format(self.lipid))
            ncol_start = 0
            row = []
            col = []
            num_of_lipids = []
            T_total = []
            for traj_idx, trajfile in enumerate(self.trajfile_list):
                print("\n########## Start calculation of {} interaction in \n########## {} \n".format(self.lipid, self.trajfile_list[traj_idx]))
                f.write("\n###### Start calculation of {} interaction in \n###### {} \n".format(self.lipid, self.trajfile_list[traj_idx]))
                traj = md.load(trajfile, top=grofile_list[traj_idx])
                lipid_haystack = get_atom_index_for_lipid(self.lipid, traj, part=self.lipid_atoms)
                lipid_resi_set = atom2residue(lipid_haystack, traj)
                num_of_lipids.append(len(lipid_resi_set))
                T_total.append(traj.time[-1] * converter)
                lipid_mapping = {lipid:lipid_idx for (lipid_idx, lipid) in enumerate(lipid_resi_set)}
                ncol_per_protein = len(lipid_resi_set) * traj.n_frames
                for idx_protein in np.arange(self.nprot):
                    for resid, (residue_indices, residue) in enumerate(zip(self.protein_residue_indices_set[idx_protein], self.residue_set)):
                        contact_residues_low, contact_residues_high = find_contact(traj, residue_indices, lipid_haystack, self.cutoff[0], self.cutoff[1])
                        col.append([ncol_start + ncol_per_protein*idx_protein+lipid_mapping[contact_lipid]*traj.n_frames+frame_idx \
                                    for frame_idx in np.arange(traj.n_frames) for contact_lipid in contact_residues_low[frame_idx] \
                                    if len(contact_residues_low[frame_idx]) > 0])
                        row.append([resid for dummy in np.arange(len(col[-1]))])
                        self.interaction_duration_raw[residue].append(Durations(contact_residues_low, contact_residues_high, traj.timestep*converter).cal_duration())
                        occupancy, lipidcount = cal_interaction_intensity(contact_residues_low)
                        self.interaction_occupancy[residue].append(occupancy)
                        self.lipid_count[residue].append(lipidcount)
                ncol_start += ncol_per_protein * self.nprot
                
                ###############################################
                ###### get some statistics for this traj ######
                ###############################################
                
                durations = np.array([np.mean(self.interaction_duration_raw[residue][-self.nprot:]) for residue in self.residue_set])
                duration_arg_idx = np.argsort(durations)[::-1]
                occupancies = np.array([np.mean(self.interaction_occupancy[residue][-self.nprot:]) for residue in self.residue_set])
                occupancy_arg_idx = np.argsort(occupancies)[::-1]
                lipidcounts =  np.array([np.mean(self.lipid_count[residue][-self.nprot:]) for residue in self.residue_set])
                lipidcount_arg_idx = np.argsort(lipidcounts)[::-1]
                log_text = "For protein ID: {}\n10 residues that showed longest interaction (and their raw interaction durations):\n".format(int(idx_protein))
                for residue, duration in zip(self.residue_set[duration_arg_idx][:10], durations[duration_arg_idx][:10]):
                    log_text += "{:^8s} -- {:^8.3f}\n".format(residue, duration)
                log_text += "10 residues that showed highest lipid occupancy:\n"
                for residue, occupancy in zip(self.residue_set[occupancy_arg_idx][:10], occupancies[occupancy_arg_idx][:10]):
                    log_text += "{:^8s} -- {:^8.2f}\n".format(residue, occupancy)
                log_text += "10 residues that have the largest number of surrounding lipids:\n"
                for residue, lipidcount in zip(self.residue_set[lipidcount_arg_idx][:10], lipidcounts[lipidcount_arg_idx][:10]):
                    log_text += "{:^8s} -- {:^8.2f}\n".format(residue, lipidcount)
                print(log_text)
                f.write(log_text)

            row = np.concatenate(row)
            col = np.concatenate(col)
            data = [1 for dummy in np.arange(len(row))]
            contact_info = coo_matrix((data, (row, col)), shape=(len(self.residue_set), ncol_start))
            self.interaction_covariance = sparse_corrcoef(contact_info)

        ##########################################
        ############ calculate koffs #############
        ##########################################
        
        for residue in self.residue_set:
            duration_raw = np.concatenate(self.interaction_duration_raw[residue])
            if np.sum(duration_raw) > 0 and len(duration_raw) > 10:
                delta_t_range = np.arange(0, T_total[traj_idx], 10) if self.timeunit == "ns" else np.arange(0, T_total[traj_idx], 0.01)
                self.sigmas[residue] = cal_sigma(duration_raw, np.mean(num_of_lipids), np.mean(T_total), delta_t_range)
                restime, koff, A = cal_restime_koff(self.sigmas[residue], initial_guess)
                self.koff[residue] = koff
                self.interaction_duration[residue] = restime
                self.A[residue] = A
            else:
                delta_t_range = np.arange(0, T_total[0], 10) if self.timeunit == "ns" else np.arange(0, T_total[0], 0.01)
                values = np.zeros(len(delta_t_range))
                self.sigmas[residue] = {key:value for key, value in zip(delta_t_range, values)}
                self.koff[residue] = 0.0
                self.interaction_duration[residue] = 0.0
                self.A[residue] = 0.0


        if plot_koff:
            koff_dir = check_dir(self.save_dir, "koff_{}".format(self.lipid))
            for residue in self.residue_set:
                durations_raw = np.concatenate(self.interaction_duration_raw[residue])
                if np.sum(duration_raw) > 0 and len(duration_raw) > 10:
                    graph_koff(durations_raw, self.sigmas[residue], self.koff[residue], self.A[residue], self.timeunit, residue, "{}/{}_{}.tiff".format(koff_dir, self.lipid, residue))
 
        
        ##############################################
        ########## wrapping up dataset ###############
        ##############################################
        
        dataset = pd.DataFrame({"Residue": [residue for residue in self.residue_set],
                                "Occupancy": np.array([np.mean(self.interaction_occupancy[residue]) \
                                                       for residue in self.residue_set]),
                                "Occupancy_std": np.array([np.std(self.interaction_occupancy[residue]) \
                                                           for residue in self.residue_set]),
                                "Duration raw": np.array([np.mean(np.concatenate(self.interaction_duration_raw[residue])) \
                                                      for residue in self.residue_set]),
                                "Duration raw_std": np.array([np.std(np.concatenate(self.interaction_duration_raw[residue])) \
                                                          for residue in self.residue_set]),
                                "Duration corrected": np.array([self.interaction_duration[residue] \
                                                                for residue in self.residue_set]),
                                "LipidCount": np.array([np.mean(self.lipid_count[residue]) \
                                                         for residue in self.residue_set]),
                                "LipidCount_std": np.array([np.std(self.lipid_count[residue]) \
                                                             for residue in self.residue_set]),
                                "Koff": np.array([self.koff[residue] for residue in self.residue_set])})
    
        dataset.to_csv("{}/Lipid_interactions_{}.csv".format(self.save_dir, self.lipid), index=False)
        self.dataset = dataset

        reminder = """
NOTE:
Occupancy:     percentage of frames where lipid is in contact
               with the given residue (0-100%);
Duration:      Average length of a continuous interaction of lipid
               with the given residue (in unit of {timeunit});
LipidCount:    Average number of lipid surrounding the given residue within the shorter cutoff;
Koff:          Koff of lipid with the given residue (in unit of ({timeunit})^(-1));
                """.format(**{"timeunit": self.timeunit})
        print(reminder)
        print()
        
        if save_dataset:
            dataset_dir = check_dir(self.save_dir, "dataset")
            with open("{}/interaction_duration_{}_corrected.pickle".format(dataset_dir, self.lipid), "wb") as f:
                pickle.dump(self.interaction_duration, f, 2)
            with open("{}/interaction_duration_{}_raw.pickle".format(dataset_dir, self.lipid), "wb") as f:
                pickle.dump(self.interaction_duration_raw, f, 2)
            with open("{}/interaction_occupancy_{}.pickle".format(dataset_dir, self.lipid), "wb") as f:
                pickle.dump(self.interaction_occupancy, f, 2)
            with open("{}/koff_{}.pickle".format(dataset_dir, self.lipid), "wb") as f:
                pickle.dump(self.koff, f, 2)

        return



    def cal_interaction_network(self, save_dir=None):
        if save_dir == None:
            save_dir = check_dir(self.save_dir, "interaction_network_{}".format(self.lipid))
        else:
            save_dir = check_dir(save_dir, "interaction_network_{}".format(self.lipid))
        residue_interaction_strength = np.array((self.dataset["Duration corrected"]))
#        residue_interaction_strength *= 1000 / np.array(residue_interaction_strength)
        interaction_covariance = np.nan_to_num(self.interaction_covariance)
        #### refined network ###
        ##### determine cov_cutoff #####
        f = open("{}/BindingSites_Info_{}.txt".format(save_dir, self.lipid), "w")
        ##### write out info ######
        reminder = """
# Occupancy: percentage of frames where lipid is in contact with the given residue (0-100%);
# Duration: average length of a continuous interaction of lipid with the given residue (in unit of {timeunit});
# Koff: Koff of lipid with the given residue (in unit of ({timeunit})^(-1));
        """.format(**{"timeunit": self.timeunit})
        f.write(reminder)
        f.write("\n")
        binding_site_id = 0

        covariance_network =np.copy(interaction_covariance)
        residue_network_raw = nx.Graph(covariance_network)
        part = community.best_partition(residue_network_raw, weight='weight')
        values = [part.get(node) for node in residue_network_raw.nodes()]
        binding_site_identifiers = np.ones(len(self.residue_set)) * 999
        for value in range(max(values)):
            node_list = [k for k,v in part.items() if v == value]
            binding_site_identifiers[node_list] = value
            if len(node_list) == 1:
                continue
            int_strength = residue_interaction_strength[node_list]
            subcommunity = nx.subgraph(residue_network_raw, node_list)
            graph_network(subcommunity,'{}/binding_site_{}'.format(save_dir, binding_site_id), \
                        interaction_strength=int_strength, node_labels=self.residue_set[node_list])

            f.write("# Binding site {}\n".format(binding_site_id))
            f.write("{:^15s}{:^15s}{:^20s}{:^15s}{:^15s}{:^15s}{:^15s}{:^15s}{:^15s}\n".format("Residue", "Duration raw", "Duration raw std", \
                    "Duration corrected", "Occupancy", "Occupancy std", "Lipid Count", "Lipid Count std", "Koff"))
            for residue in self.residue_set[node_list]:
                f.write("{Residue:^15s}{Duration raw:^15.3f}{Duration raw_std:^20.3f}{Duration corrected:^15.3f}{Occupancy:^15.3f}{Occupancy_std:^15.3f}{LipidCount:^15f}{LipidCount_std:^15f}{Koff:^15.5f}\n".format(\
                        **self.dataset[self.dataset["Residue"]==residue].to_dict("records")[0] ))
            f.write("\n")
            with open("{}/graph_bindingsite_{}.pickle".format(save_dir, binding_site_id), "wb") as filehandler:
                pickle.dump(subcommunity, filehandler, 2)
            binding_site_id += 1
        f.close()
        
        self.dataset["Binding site"]  = binding_site_identifiers
        self.dataset.to_csv("{}/Lipid_interactions_{}.csv".format(self.save_dir, self.lipid), index=False)
        return


    def plot_interactions(self, item="Duration raw", helix_regions=[], save_dir=None):
        if save_dir == None:
            save_dir = self.save_dir
        else:
            save_dir = save_dir
        if not os.path.isdir(save_dir):
            os.mkdir(save_dir)
        data = self.dataset[item]
        resi = np.arange(len(data)) + self.resi_offset + 1
        width = 1
        sns.set_style("ticks", {'xtick.major.size': 5.0, 'ytick.major.size': 5.0})
        fig, ax = plt.subplots(1, 1, figsize=(4.5,2.8))
        ax.bar(resi, data, width, linewidth=0, color=sns.xkcd_rgb["red"])
        sns.despine(fig, top=True, right=True, trim=False)
        ax.set_xticks(np.arange(0, len(data) + 1, 50))
        ax.set_xlabel("Residue", fontsize=10, weight="bold")
        if self.timeunit == "ns":
            timeunit = " (ns) "
        elif self.timeunit == "us":
            timeunit = r" ($\mu s$)"
        if item == "Duration raw":
            ylabel = item + timeunit
        elif item == "Duration corrected":
            ylabel = item + timeunit
        elif item == "Occupancy":
            ylabel = item + " 100% "
        elif item == "LipidCount":
            ylabel = "Num. of Lipids"
        ax.set_ylabel(ylabel, fontsize=10, weight="bold")
        for label in ax.xaxis.get_ticklabels() + ax.yaxis.get_ticklabels():
            plt.setp(label, fontsize=10, weight="bold")
        ylim = ax.get_ylim()
        if len(helix_regions) > 0:
            identify_helix_region(ax, ylim, helix_regions)
        ax.set_title("{} {}".format(self.lipid, item), fontsize=10, weight="bold")
        plt.tight_layout()
        plt.savefig("{}/{}_{}.tiff".format(save_dir, "_".join(item.split()), self.lipid), dpi=200)
        plt.close()
        return



##########################################
########### start calculation ############
##########################################

trajfile_list = args.f
grofile_list = args.c
lipid_set = args.lipids
print(args.cutoffs)
cutoff = [float(data) for data in args.cutoffs]
for lipid in lipid_set:
    li = LipidInteraction(trajfile_list, grofile_list, cutoff=cutoff, lipid=lipid, lipid_atoms=args.lipid_atoms, nprot=args.nprot, timeunit=args.tu, \
                          natoms_per_protein=args.natoms_per_protein, resi_offset=args.resi_offset, save_dir=args.save_dir)
    li.cal_interactions(plot_koff=args.plot_koff, save_dataset=args.save_dataset)
    li.cal_interaction_network()
    if len(args.helix_regions) > 0:
        helix_regions = []
        for pair in args.helix_regions:
            helix_regions.append([])
            for num in pair.split(","):
                helix_regions[-1].append(int(num))
    else:
        helix_regions = []
    li.plot_interactions(item="Duration raw", helix_regions=helix_regions)
    li.plot_interactions(item="Duration corrected", helix_regions=helix_regions)
    li.plot_interactions(item="Occupancy", helix_regions=helix_regions)
    li.plot_interactions(item="LipidCount", helix_regions=helix_regions)


###########################################
###########################################

#trajfile_list = []
#grofile_list = []
#for num in np.arange(2)+1:
#    trajfile_list.append("/sansom/s121/bioc1467/Work/GPCR/monomer/A2a/3eml_{}/md_fit_3to8us.xtc".format(num))
#    grofile_list.append("/sansom/s121/bioc1467/Work/GPCR/monomer/A2a/3eml_{}/protein_lipids.gro".format(num))
#
#lipid="POP2"
#li = LipidInteraction(trajfile_list, grofile_list, lipid=lipid, nprot=1, resi_offset=2, timeunit="ns", \
#                      save_dir="/sansom/s121/bioc1467/Work/GPCR/monomer/A2a")
#li.cal_interactions()
#li.cal_interaction_network()


        
