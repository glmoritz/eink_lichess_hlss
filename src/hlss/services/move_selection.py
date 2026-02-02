"""Helpers for move-selection logic shared across services."""

from __future__ import annotations

import chess


class MoveSelectionHelper:
    """Utility methods for move-selection workflows."""

    @staticmethod
    def get_piece_moves(board: chess.Board, piece: str) -> list[chess.Move]:
        """Return legal moves for the selected piece type."""
        piece_type = chess.PIECE_SYMBOLS.index(piece.lower())
        moves: list[chess.Move] = []
        for move in board.legal_moves:
            from_piece = board.piece_at(move.from_square)
            if from_piece and from_piece.piece_type == piece_type:
                moves.append(move)
        return moves

    @staticmethod
    def get_file_options_for_piece(board: chess.Board, piece: str) -> list[str]:
        """Return available file options for a piece type."""
        moves = MoveSelectionHelper.get_piece_moves(board, piece)
        file_indices = {chess.square_file(m.to_square) for m in moves}
        if piece.upper() == "P":
            file_indices.update(chess.square_file(m.from_square) for m in moves)
        return [chr(ord("a") + i) for i in sorted(file_indices)]

    @staticmethod
    def get_rank_options_for_piece_and_file(
        board: chess.Board, piece: str, selected_file: str
    ) -> list[int]:
        """Return available rank options for a piece type and file selection."""
        if not selected_file:
            return []
        moves = MoveSelectionHelper.get_piece_moves(board, piece)
        file_idx = ord(selected_file) - ord("a")
        rank_indices = {
            chess.square_rank(m.to_square)
            for m in moves
            if chess.square_file(m.to_square) == file_idx
        }
        if piece.upper() == "P":
            for move in moves:
                if chess.square_file(move.from_square) == file_idx:
                    rank_indices.add(chess.square_rank(move.to_square))
        return [r + 1 for r in sorted(rank_indices)]

    @staticmethod
    def get_matching_moves_for_selection(
        board: chess.Board, piece: str, selected_file: str, rank: int
    ) -> list[chess.Move]:
        """Return legal moves that match a piece + file + rank selection."""
        target_square = f"{selected_file}{rank}"
        piece_type = chess.PIECE_SYMBOLS.index(piece.lower())
        selected_file_idx = ord(selected_file) - ord("a")

        matching_moves: list[chess.Move] = []
        for move in board.legal_moves:
            if chess.square_name(move.to_square) != target_square:
                continue
            from_piece = board.piece_at(move.from_square)
            if not from_piece:
                continue
            if from_piece.piece_type != piece_type:
                continue
            matching_moves.append(move)

        if piece_type == chess.PAWN:
            for move in board.legal_moves:
                if (
                    chess.square_file(move.from_square) == selected_file_idx
                    and chess.square_rank(move.to_square) == (rank - 1)
                    and board.piece_at(move.from_square).piece_type == chess.PAWN
                ):
                    if move not in matching_moves:
                        matching_moves.append(move)

        return matching_moves
