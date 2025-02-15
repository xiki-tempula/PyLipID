# PyLipID

## Introduction 
**pylipid.py**: is a toolkit to calculate lipid interactions with membrane proteins. 
It calculates: 
- lipid interactions with the proteins in terms of their duration, occupancy, num. of lipids surrounding given residues and koff;
- lipid binding sites via interaction networks. 

It plots:
- lipid interaction with the protein as a function of protein residues. 
- the calculated lipid koff to each protein residue. 
- interaction network of lipid binding sites. 

## Requirements:
- [mdtraj](http://mdtraj.org)
- [networkx](https://networkx.github.io)
- [seaborn](https://seaborn.pydata.org)
- [community](https://python-louvain.readthedocs.io/en/latest/index.html)
- [pandas](https://pandas.pydata.org)
- [scipy](https://www.scipy.org)
- [matplotlib](https://matplotlib.org)

[Anaconda](https://www.anaconda.com/distribution/) is recommended to manage python libraries. 


## Usage:

**-f**: Trajectories to check. Can be a list of trajectories with similar system settings. Read in by mdtraj.load().

**-c**: structural information of the trajectories given to -f. Read in by mdtraj.load(). Supported format include gro, pdb xyz, etc. 

**-tu**: time unit of all the calculations. Available options include ns and us. 

**-save_dir**: directory where all the results will be located. Will use current working directory if not specified. 

**-cutoffs**: the double cutoffs used to define lipid interactions. A continuous lipid contact with a given residue starts when the lipid
gets closer to the given residue than the smaller cutoff and ends when the lipid gets farther than the larger cutoff. 

**-lipids**: specify the lipid residue name 

**-lipid_atoms**: specify the atoms to check

**-nprot**: num. of proteins in the system

**-resi_offset**: Shift the residue index of the protein. Can be useful when a protein with missing residues at its N-terminus was martinized 
to Martini force field, as martinize.py shift the residue index of the first residue to 1 regardless of its original index. 

**-plot_koff**: plot koff values for each residue based on the conglomerate interaction durations from all trajectories. This means the koff is an average over all trajectories. A directory koff_{lipid} will be generated for each lipid species.

**-save_dataset**: save dataset in pickle. 

Usage example: 
```
python pylipid.py -f ./run_1/md.xtc ./run_2/md.xtc -c ./run_1/protein_lipids.gro ./run_2/protein_lipids.gro 
-cutoffs 0.55 1.4 -lipids POPC CHOL POP2 -nprot 1 -resi_offset 5 -plot_koff -save_dataset
```
For phospholipids, it's recommended to use only the headgroup atoms to detect lipid binding sites:
```
python pylipid.py -f ./run_1/md.xtc ./run_2/md.xtc -c ./run_1/protein_lipids.gro ./run_2/protein_lipids.gro 
-cutoffs 0.55 1.4 -lipids POP2 -lipid_atoms C1 C2 C3 C4 PO4 P1 P2 -nprot 1 -resi_offset 5 -plot_koff -save_dataset
```

