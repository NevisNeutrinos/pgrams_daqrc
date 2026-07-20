import numpy as np 
import pandas as pd

#GOAL: Return x and y coordinates, organized by shaper number:

def coord_mapping(bs=1, steps_per_bank=29):

    bank_size = bs + steps_per_bank

    #Use -159 as placeholder for missing value, and use -100 as placeholder for missing channel:
    #Hardcode the mapping for slot 13. 1st col is channel, 2nd col is bank, 3rd col is sig#
    channel_to_bank_sig_first = np.array([[0,-100,-100],
                            [1,'K',12],
                            [2,-100,-100],
                            [3,'K',11],
                            [4,-100,-100],
                            [5,'K',14],
                            [6,-100,-100],
                            [7,'K',13],
                            [8,-100,-100],
                            [9,-100,-100],
                            [10,'K',0],
                            [11,'L',14],
                            [12,'K',1],
                            [13,'L',0],
                            [14,'K',2],
                            [15,'L',1],
                            [16,'K',3],
                            [17,'L',2],
                            [18,'K',4],
                            [19,'L',3],
                            [20,'K',5],
                            [21,'L',4],
                            [22,'K',6],
                            [23,'L',5],
                            [24,'K',7],
                            [25,'L',6],
                            [26,'K',8],
                            [27,'L',7],
                            [28,'K',9],
                            [29,'L',8],
                            [30,-100,-100],
                            [31,-100,-100],

    ])

    #This pattern repeats with 1st col increasing by 32 at each step, and the following permutations K -> E -> I -> C -> G -> A and L -> F -> J -> D -> H -> B
    channel_to_bank_sig_second = channel_to_bank_sig_first.copy()
    K_mask = (channel_to_bank_sig_second[:,1] == 'K')
    L_mask = (channel_to_bank_sig_second[:,1] == 'L')
    channel_to_bank_sig_second[:,0] = channel_to_bank_sig_second[:,0].astype('i8') + 32
    channel_to_bank_sig_second[K_mask,1] = 'E'
    channel_to_bank_sig_second[L_mask,1] = 'F'
    
    channel_to_bank_sig_third = channel_to_bank_sig_second.copy()
    E_mask = (channel_to_bank_sig_third[:,1] == 'E')
    F_mask = (channel_to_bank_sig_third[:,1] == 'F')
    channel_to_bank_sig_third[:,0] = channel_to_bank_sig_third[:,0].astype('i8') + 32
    channel_to_bank_sig_third[E_mask,1] = 'I'
    channel_to_bank_sig_third[F_mask,1] = 'J'

    channel_to_bank_sig_fourth = channel_to_bank_sig_third.copy()
    I_mask = (channel_to_bank_sig_fourth[:,1] == 'I')
    J_mask = (channel_to_bank_sig_third[:,1] == 'J')
    channel_to_bank_sig_fourth[:,0] = channel_to_bank_sig_fourth[:,0].astype('i8') + 32
    channel_to_bank_sig_fourth[I_mask,1] = 'C'
    channel_to_bank_sig_fourth[J_mask,1] = 'D'

    channel_to_bank_sig_fifth = channel_to_bank_sig_fourth.copy()
    C_mask = (channel_to_bank_sig_fifth[:,1] == 'C')
    D_mask = (channel_to_bank_sig_fifth[:,1] == 'D')
    channel_to_bank_sig_fifth[:,0] = channel_to_bank_sig_fifth[:,0].astype('i8') + 32
    channel_to_bank_sig_fifth[C_mask,1] = 'G'
    channel_to_bank_sig_fifth[D_mask,1] = 'H'

    channel_to_bank_sig_sixth = channel_to_bank_sig_fifth.copy()
    G_mask = (channel_to_bank_sig_sixth[:,1] == 'G')
    H_mask = (channel_to_bank_sig_sixth[:,1] == 'H')
    channel_to_bank_sig_sixth[:,0] = channel_to_bank_sig_sixth[:,0].astype('i8') + 32
    channel_to_bank_sig_sixth[G_mask,1] = 'A'
    channel_to_bank_sig_sixth[H_mask,1] = 'B'

    channel_tbs = np.vstack((channel_to_bank_sig_first,
                                    channel_to_bank_sig_second,
                                    channel_to_bank_sig_third,
                                    channel_to_bank_sig_fourth,
                                    channel_to_bank_sig_fifth,
                                    channel_to_bank_sig_sixth))

    #Now, we have a collection of shaper channels and their corresponding bank and SIG
    #We need to write a script to determine 1: whether a channel is an X or a Y channel, and where it is located:

    pos_list = np.full(channel_tbs.shape, -159)
    pos_list[:,0] = channel_tbs[:,0].copy()
    #A banks, y channels, starting from 0:
    A_mask = (channel_tbs[:,1] == 'A')
    pos_list[A_mask,2] = (2*channel_tbs[A_mask,2].astype('i8')) + 2*bank_size #2 full banks between bank A and y=0

    #B banks, y channels, starting from 0: 
    B_mask = (channel_tbs[:,1] == 'B')
    pos_list[B_mask,2] = (2*channel_tbs[B_mask,2].astype('i8')) + bank_size

    #C banks, y channels, starting from 0:
    C_mask = (channel_tbs[:,1] == 'C')
    pos_list[C_mask,2] = (2*channel_tbs[C_mask,2].astype('i8'))

    #L banks, x channels, starting from 0:
    L_mask = (channel_tbs[:,1] == 'L')
    pos_list[L_mask,1] = (2*channel_tbs[L_mask,2].astype('i8'))

    #K banks, x channels, starting from 0:
    K_mask = (channel_tbs[:,1] == 'K')
    pos_list[K_mask,1] = (2*channel_tbs[K_mask,2].astype('i8')) + bank_size

    #J banks, x channels, starting from 0:
    J_mask = (channel_tbs[:,1] == 'J')
    pos_list[J_mask,1] = (2*channel_tbs[J_mask,2].astype('i8')) + 2*bank_size
    
    #I banks, y channels, starting from 89:
    I_mask = (channel_tbs[:,1] == 'I')
    pos_list[I_mask,2] = (3*bank_size - 1) - (2*channel_tbs[I_mask,2].astype('i8'))

    #H banks, y channels, starting from 89:
    H_mask = (channel_tbs[:,1] == 'H')
    pos_list[H_mask,2] = (3*bank_size - 1) - ((2*channel_tbs[H_mask,2].astype('i8')) + bank_size)

    #G banks, y channels, starting from 89:
    G_mask = (channel_tbs[:,1] == 'G')
    pos_list[G_mask,2] = (3*bank_size - 1) - ((2*channel_tbs[G_mask,2].astype('i8')) + 2*bank_size)

    #F banks, x channels, starting from 89:
    F_mask = (channel_tbs[:,1] == 'F')
    pos_list[F_mask,1] = (3*bank_size - 1) - (2*channel_tbs[F_mask,2].astype('i8'))

    #E banks, x channels, starting from 89:
    E_mask = (channel_tbs[:,1] == 'E')
    pos_list[E_mask,1] = (3*bank_size - 1) - ((2*channel_tbs[E_mask,2].astype('i8')) + bank_size)

    #D banks, x channels, starting from 89:
    D_mask = (channel_tbs[:,1] == 'D')
    pos_list[D_mask,1] = (3*bank_size - 1) - ((2*channel_tbs[D_mask,2].astype('i8')) + 2*bank_size)


    # x_arr = pos_list[:,1][pos_list[:,1] >= 0]
    # y_arr = pos_list[:,2][pos_list[:,2] >= 0]

    # print(f'Number of x channels: {x_arr.size}')
    # print(f'Number of y channels: {y_arr.size}')
    # print(f'Total number of channels: {x_arr.size + y_arr.size}')
    # print(f'Expected number of channels: {25*6}')

    return pos_list

def get_bank_sig_pos_chan(bs=1, steps_per_bank=29):

    bank_size = bs + steps_per_bank

    #Use -159 as placeholder for missing value, and use -100 as placeholder for missing channel:
    #Hardcode the mapping for slot 13. 1st col is channel, 2nd col is bank, 3rd col is sig#
    channel_to_bank_sig_first = np.array([[0,-100,-100],
                            [1,'K',12],
                            [2,-100,-100],
                            [3,'K',11],
                            [4,-100,-100],
                            [5,'K',14],
                            [6,-100,-100],
                            [7,'K',13],
                            [8,-100,-100],
                            [9,-100,-100],
                            [10,'K',0],
                            [11,'L',14],
                            [12,'K',1],
                            [13,'L',0],
                            [14,'K',2],
                            [15,'L',1],
                            [16,'K',3],
                            [17,'L',2],
                            [18,'K',4],
                            [19,'L',3],
                            [20,'K',5],
                            [21,'L',4],
                            [22,'K',6],
                            [23,'L',5],
                            [24,'K',7],
                            [25,'L',6],
                            [26,'K',8],
                            [27,'L',7],
                            [28,'K',9],
                            [29,'L',8],
                            [30,-100,-100],
                            [31,-100,-100],

    ])

    #This pattern repeats with 1st col increasing by 32 at each step, and the following permutations K -> E -> I -> C -> G -> A and L -> F -> J -> D -> H -> B
    channel_to_bank_sig_second = channel_to_bank_sig_first.copy()
    K_mask = (channel_to_bank_sig_second[:,1] == 'K')
    L_mask = (channel_to_bank_sig_second[:,1] == 'L')
    channel_to_bank_sig_second[:,0] = channel_to_bank_sig_second[:,0].astype('i8') + 32
    channel_to_bank_sig_second[K_mask,1] = 'E'
    channel_to_bank_sig_second[L_mask,1] = 'F'
    
    channel_to_bank_sig_third = channel_to_bank_sig_second.copy()
    E_mask = (channel_to_bank_sig_third[:,1] == 'E')
    F_mask = (channel_to_bank_sig_third[:,1] == 'F')
    channel_to_bank_sig_third[:,0] = channel_to_bank_sig_third[:,0].astype('i8') + 32
    channel_to_bank_sig_third[E_mask,1] = 'I'
    channel_to_bank_sig_third[F_mask,1] = 'J'

    channel_to_bank_sig_fourth = channel_to_bank_sig_third.copy()
    I_mask = (channel_to_bank_sig_fourth[:,1] == 'I')
    J_mask = (channel_to_bank_sig_third[:,1] == 'J')
    channel_to_bank_sig_fourth[:,0] = channel_to_bank_sig_fourth[:,0].astype('i8') + 32
    channel_to_bank_sig_fourth[I_mask,1] = 'C'
    channel_to_bank_sig_fourth[J_mask,1] = 'D'

    channel_to_bank_sig_fifth = channel_to_bank_sig_fourth.copy()
    C_mask = (channel_to_bank_sig_fifth[:,1] == 'C')
    D_mask = (channel_to_bank_sig_fifth[:,1] == 'D')
    channel_to_bank_sig_fifth[:,0] = channel_to_bank_sig_fifth[:,0].astype('i8') + 32
    channel_to_bank_sig_fifth[C_mask,1] = 'G'
    channel_to_bank_sig_fifth[D_mask,1] = 'H'

    channel_to_bank_sig_sixth = channel_to_bank_sig_fifth.copy()
    G_mask = (channel_to_bank_sig_sixth[:,1] == 'G')
    H_mask = (channel_to_bank_sig_sixth[:,1] == 'H')
    channel_to_bank_sig_sixth[:,0] = channel_to_bank_sig_sixth[:,0].astype('i8') + 32
    channel_to_bank_sig_sixth[G_mask,1] = 'A'
    channel_to_bank_sig_sixth[H_mask,1] = 'B'

    channel_tbs = np.vstack((channel_to_bank_sig_first,
                                    channel_to_bank_sig_second,
                                    channel_to_bank_sig_third,
                                    channel_to_bank_sig_fourth,
                                    channel_to_bank_sig_fifth,
                                    channel_to_bank_sig_sixth))

    #Now, we have a collection of shaper channels and their corresponding bank and SIG
    #We need to write a script to determine 1: whether a channel is an X or a Y channel, and where it is located:

    pos_list = np.full((channel_tbs.shape[0],6), "NA", dtype='<U8')
    pos_list[:,0] = channel_tbs[:,1].copy()
    pos_list[:,1] = channel_tbs[:,2].copy()
    pos_list[:,4] = (channel_tbs[:,0].copy().astype('i8') // 64) + 1
    pos_list[:,5] = channel_tbs[:,0].copy().astype('i8') % 64

    #A banks, y channels, starting from 0:
    A_mask = (channel_tbs[:,1] == 'A')
    pos_list[A_mask,3] = (2*channel_tbs[A_mask,2].astype('i8')) + 2*bank_size #2 full banks between bank A and y=0

    #B banks, y channels, starting from 0: 
    B_mask = (channel_tbs[:,1] == 'B')
    pos_list[B_mask,3] = (2*channel_tbs[B_mask,2].astype('i8')) + bank_size

    #C banks, y channels, starting from 0:
    C_mask = (channel_tbs[:,1] == 'C')
    pos_list[C_mask,3] = (2*channel_tbs[C_mask,2].astype('i8'))

    #L banks, x channels, starting from 0:
    L_mask = (channel_tbs[:,1] == 'L')
    pos_list[L_mask,2] = (2*channel_tbs[L_mask,2].astype('i8'))

    #K banks, x channels, starting from 0:
    K_mask = (channel_tbs[:,1] == 'K')
    pos_list[K_mask,2] = (2*channel_tbs[K_mask,2].astype('i8')) + bank_size

    #J banks, x channels, starting from 0:
    J_mask = (channel_tbs[:,1] == 'J')
    pos_list[J_mask,2] = (2*channel_tbs[J_mask,2].astype('i8')) + 2*bank_size
    
    #I banks, y channels, starting from 89:
    I_mask = (channel_tbs[:,1] == 'I')
    pos_list[I_mask,3] = (3*bank_size - 1) - (2*channel_tbs[I_mask,2].astype('i8'))

    #H banks, y channels, starting from 89:
    H_mask = (channel_tbs[:,1] == 'H')
    pos_list[H_mask,3] = (3*bank_size - 1) - ((2*channel_tbs[H_mask,2].astype('i8')) + bank_size)

    #G banks, y channels, starting from 89:
    G_mask = (channel_tbs[:,1] == 'G')
    pos_list[G_mask,3] = (3*bank_size - 1) - ((2*channel_tbs[G_mask,2].astype('i8')) + 2*bank_size)

    #F banks, x channels, starting from 89:
    F_mask = (channel_tbs[:,1] == 'F')
    pos_list[F_mask,2] = (3*bank_size - 1) - (2*channel_tbs[F_mask,2].astype('i8'))

    #E banks, x channels, starting from 89:
    E_mask = (channel_tbs[:,1] == 'E')
    pos_list[E_mask,2] = (3*bank_size - 1) - ((2*channel_tbs[E_mask,2].astype('i8')) + bank_size)

    #D banks, x channels, starting from 89:
    D_mask = (channel_tbs[:,1] == 'D')
    pos_list[D_mask,2] = (3*bank_size - 1) - ((2*channel_tbs[D_mask,2].astype('i8')) + 2*bank_size)

    pos_list[:,1] = pos_list[:,1].astype('i8') + 1

    bank_sig_sort = np.lexsort((pos_list[:,1].astype('i8'), pos_list[:,0]))
    pos_list = pos_list[bank_sig_sort,:]

    x_mask = (pos_list[:,2] != 'NA')
    y_mask = (pos_list[:,3] != 'NA')


    x_points = pos_list[x_mask,:]
    y_points = pos_list[y_mask,:]


    # x_arr = pos_list[:,1][pos_list[:,1] >= 0]
    # y_arr = pos_list[:,2][pos_list[:,2] >= 0]

    # print(f'Number of x channels: {x_arr.size}')
    # print(f'Number of y channels: {y_arr.size}')
    # print(f'Total number of channels: {x_arr.size + y_arr.size}')
    # print(f'Expected number of channels: {25*6}')

    return pos_list

# x, y = get_bank_sig_pos_chan()

# print(f'x coords:\n{x}\n')
# print(f'y coords:\n{y}')