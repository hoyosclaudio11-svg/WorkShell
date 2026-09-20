"""Ventana de conejillo para probar las miniaturas: un dibujo con color, para
que la captura tenga algo que mostrar y se distinga de un cuadro negro.

    python _conejo_miniaturas.py [titulo]

Se cierra sola cuando su ventana se cierra (o cuando la mata quien la lanzó).
"""
import sys
import tkinter as tk

TITULO = sys.argv[1] if len(sys.argv) > 1 else 'Conejo Miniaturas WorksheLL'

raiz = tk.Tk()
raiz.title(TITULO)
raiz.geometry('520x320+120+120')
lienzo = tk.Canvas(raiz, width=520, height=320, bg='#123b2e', highlightthickness=0)
lienzo.pack(fill='both', expand=True)
lienzo.create_oval(40, 40, 260, 260, fill='#3FBFB0', outline='#B08A24', width=6)
lienzo.create_rectangle(280, 60, 480, 260, fill='#CE4E93', outline='')
lienzo.create_text(260, 290, text=TITULO, fill='#F2E9D8',
                   font=('Segoe UI', 12, 'bold'))
raiz.mainloop()
