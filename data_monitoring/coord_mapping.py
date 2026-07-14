import numpy as np 
import pandas as pd

#GOAL: Given a list of points ordered by shaper channel, reorganize them to be in order of increasing FEM channel

def coord_mapping(bs=3):
    base_channels = np.array((21,1,23,3,25,5,27,7,29,9,2,11,4,13,6,15,8,17,10,19,12,14,16,18,20,22,24,26,28,30))

    offsets = np.arange(0, 180, 30)[:,None]

    total_channels = (base_channels + offsets).flatten() - 1
    
    x_channels_list = np.concatenate((total_channels[:45], total_channels[90:135]))
    y_channels_list = np.concatenate((total_channels[45:90], total_channels[135:]))

    #Make bank H (E) positions relative to bank I (F). We add IF_pos.max() to start H (E) relative to I (F), and we add the bs (bank spacing) to that value to get the starting value of H (E)
    #Since IF_pos just defines the spacing b/w adjacent wires, can reuse it for other spacings:
    IF_pos = np.arange(0,30,2)
    HE_pos = IF_pos + IF_pos.max() + bs
    GD_pos = IF_pos + HE_pos.max() + bs

    #Banks A (J) ,B (K), C (L) are just shifted 1 wire-unit to the right (up) of I (F), H (E), G (D).
    AJ_pos = IF_pos + 1
    BK_pos = HE_pos + 1
    CL_pos = GD_pos + 1

    #Concatenate the position arrays, and make sure they are ordered exactly as x_channels_list is ordered:
    x_map = np.concatenate((AJ_pos,BK_pos,CL_pos,GD_pos,HE_pos,IF_pos))

    #Make mapping for y, as well, ensuring the ordering is correct:
    y_map = np.concatenate((GD_pos,HE_pos,IF_pos,AJ_pos,BK_pos,CL_pos))

    # Pre-allocate output matrix of shape (N, 2) filled with None (or np.nan), with channel number in column 0:
    pos = np.full((len(total_channels), 3), -159)
    pos[:,0] = np.arange(0,len(total_channels))


    #Find the permutation that would take our organized arrays to a sorted array:
    x_sorter = np.argsort(x_channels_list)
    y_sorter = np.argsort(y_channels_list)

    x_map = x_map[x_sorter]
    x_mask = np.isin(pos[:,0], x_channels_list)

    y_map = y_map[y_sorter]
    y_mask = np.isin(pos[:,0], y_channels_list)

    pos[:,1][x_mask] = x_map
    pos[:,2][y_mask] = y_map

    return pos