def check_winner(b):
    wins = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
    for x, y, z in wins:
        if b[x] != ' ' and b[x] == b[y] == b[z]:
            return b[x]
    return None

def play_tictactoe(moves=None):
    board = [' '] * 9
    player = 'X'
    idx = 0
    while True:
        if moves is not None:
            if idx >= len(moves):
                break
            pos = moves[idx]
            idx += 1
        else:
            pos = int(input(f"Player {player} (0-8): "))
        if board[pos] == ' ':
            board[pos] = player
            w = check_winner(board)
            if w:
                print(f"Player {w} wins!")
                return w
            if ' ' not in board:
                print("Draw!")
                return "Draw"
            player = 'O' if player == 'X' else 'X'

if __name__ == "__main__":
    play_tictactoe([0, 3, 1, 4, 2])